from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
from jose import jwt, JWTError
from fastapi.security import OAuth2PasswordBearer
from datetime import datetime, timedelta
from typing import List, Optional
import secrets

from app.core.database import get_db, AsyncSessionLocal
from app.models.user import User, Mill, UserRole
from app.core.config import settings
from app.services.email_service import queue_email, dispatch_pending_emails
from app.services.email_templates import welcome_email, password_reset_email, magic_link_email

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

pwd_context = CryptContext(schemes=["pbkdf2_sha256", "bcrypt"], deprecated="auto")


class UserRegister(BaseModel):
    email: EmailStr
    password: str
    mill_id: str
    # Self-registration creates mill_admin (admin) by default.
    # Superadmin is never self-registered — it is seeded via env vars at startup.
    # Managers are created by mill admins via the /admin/users endpoint.
    role: Optional[UserRole] = UserRole.admin


class Token(BaseModel):
    access_token: str
    token_type: str
    api_key: Optional[str] = None
    mill_id: Optional[str] = None


class LoginRequest(BaseModel):
    """Documentation-only model for Swagger — the route itself still accepts
    either JSON (this shape) or OAuth2 form-encoded fields for compatibility
    with Swagger's built-in Authorize flow."""
    email: EmailStr
    password: str


class LogoutResponse(BaseModel):
    status: str
    message: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class MagicLinkRequest(BaseModel):
    email: EmailStr


class MagicLoginRequest(BaseModel):
    token: str


class StatusResponse(BaseModel):
    status: str
    message: str


class RegisterResponse(BaseModel):
    status: str
    api_key: str
    message: str


class MillInfo(BaseModel):
    mill_id: str
    api_key: str
    has_baseline: bool


class UserProfile(BaseModel):
    id: int
    email: str
    role: str
    created_at: datetime
    mills: List[MillInfo]


def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)


async def _dispatch_outbox_now():
    """Best-effort immediate delivery right after a queueing commit. The
    periodic sweep in app.main is the safety net if this doesn't run
    (e.g. the process restarts between the commit and this task firing)."""
    async with AsyncSessionLocal() as db:
        await dispatch_pending_emails(db)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


async def _issue_token(db: AsyncSession, db_user: User) -> dict:
    """Shared by /login and /magic-login: look up the user's first mill and mint a JWT."""
    mill_result = await db.execute(select(Mill).where(Mill.user_id == db_user.id))
    first_mill = mill_result.scalars().first()
    api_key = first_mill.api_key if first_mill else None
    mill_id = first_mill.mill_id if first_mill else "N/A"

    access_token = create_access_token(
        data={"sub": db_user.email, "mill_id": mill_id, "role": db_user.role.value},
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "api_key": api_key,
        "mill_id": mill_id,
    }


async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()
    if user is None:
        raise credentials_exception
    return user


async def get_current_superadmin_user(current_user: User = Depends(get_current_user)):
    """Only the platform owner (superadmin) passes this guard."""
    if current_user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Superadmin access required")
    return current_user


async def get_current_admin_user(current_user: User = Depends(get_current_user)):
    """Mill admins and superadmin both pass this guard.
    Routes that use this dependency must additionally check
    current_user.role == UserRole.superadmin to decide whether
    to apply per-mill data scoping.
    """
    if current_user.role not in (UserRole.admin, UserRole.superadmin):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return current_user


@router.post(
    "/register",
    response_model=RegisterResponse,
    responses={
        400: {"description": "Email already registered"},
        403: {"description": "Superadmin role requested (not allowed via self-registration)"},
    },
)
async def register(user: UserRegister, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """
    Public self-registration — creates a mill admin account with one initial mill.
    Superadmin role cannot be claimed here; it is seeded at server startup via env vars.

    A welcome email (with the API key) is queued to the **transactional outbox**
    in the same DB transaction as account creation, then delivered in the
    background. See `app.services.email_service` for the send pattern — the
    row is durable the moment this call returns 200, independent of whether
    Resend delivery has actually happened yet.
    """
    if user.role == UserRole.superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin accounts cannot be self-registered",
        )

    result = await db.execute(select(User).where(User.email == user.email))
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Email already registered")

    new_user = User(
        email=user.email,
        password_hash=get_password_hash(user.password),
        role=user.role,
    )
    db.add(new_user)
    await db.flush()

    api_key = f"fsa_{user.mill_id}_{secrets.token_hex(16)}"
    new_mill = Mill(
        user_id=new_user.id,
        admin_id=new_user.id,   # self-registered admin owns their own first mill
        mill_id=user.mill_id,
        api_key=api_key,
    )
    db.add(new_mill)
    await queue_email(
        db,
        user.email,
        "Welcome to StikkVerse",
        f"Your account is ready. Your API key for mill {user.mill_id}:\n\n{api_key}\n\nKeep it secret -- it authenticates your data uploads.",
        html=welcome_email(user.mill_id, api_key),
    )
    await db.commit()
    await db.refresh(new_user)

    background_tasks.add_task(_dispatch_outbox_now)

    return {"status": "success", "api_key": api_key, "message": "Account created. Save this API key!"}


@router.post(
    "/login",
    response_model=Token,
    responses={401: {"description": "Incorrect email or password"}},
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": LoginRequest.model_json_schema(),
                    "example": {"email": "user@example.com", "password": "yourpassword"},
                }
            },
        }
    },
)
async def login(request: Request, db: AsyncSession = Depends(get_db)):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        email = body.get("email") or body.get("username")
        password = body.get("password")
    else:
        form = await request.form()
        email = form.get("email") or form.get("username")
        password = form.get("password")

    if not email or not password:
        raise HTTPException(status_code=422, detail="email and password are required")

    result = await db.execute(select(User).where(User.email == email))
    db_user = result.scalars().first()

    if not db_user or not verify_password(password, db_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await _issue_token(db, db_user)


@router.post(
    "/forgot-password",
    response_model=StatusResponse,
    responses={200: {"description": "Always returned, regardless of whether the email is registered"}},
)
async def forgot_password(
    payload: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Request a password reset link. Always returns the same message whether or
    not the email is registered, so callers can't use this endpoint to
    enumerate accounts.

    If the account exists, a single-use token (1 hour TTL) is generated and
    the reset email is queued to the outbox (same pattern as `/register`)
    and delivered in the background via Resend.
    """
    result = await db.execute(select(User).where(User.email == payload.email))
    db_user = result.scalars().first()

    if db_user:
        token = secrets.token_urlsafe(32)
        db_user.reset_token = token
        db_user.reset_token_expires = datetime.utcnow() + timedelta(hours=1)
        reset_link = f"https://stikkverse.app/reset-password?token={token}"
        await queue_email(
            db,
            db_user.email,
            "Reset your StikkVerse password",
            f"Use this link to reset your password (valid 1 hour):\n\n{reset_link}\n\n"
            "If you didn't request this, you can ignore this email.",
            html=password_reset_email(reset_link),
        )
        await db.commit()
        background_tasks.add_task(_dispatch_outbox_now)

    return {"status": "success", "message": "If that email is registered, a reset link has been sent."}


@router.post(
    "/reset-password",
    response_model=StatusResponse,
    responses={400: {"description": "Reset token is invalid, already used, or expired"}},
)
async def reset_password(payload: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    """Consume the token from `/forgot-password` and set a new password. The
    token is single-use: it's cleared on success, so a second request with
    the same token gets a 400."""
    result = await db.execute(select(User).where(User.reset_token == payload.token))
    db_user = result.scalars().first()

    if (
        not db_user
        or not db_user.reset_token_expires
        or db_user.reset_token_expires < datetime.utcnow()
    ):
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    db_user.password_hash = get_password_hash(payload.new_password)
    db_user.reset_token = None
    db_user.reset_token_expires = None
    await db.commit()

    return {"status": "success", "message": "Password has been reset"}


@router.post(
    "/magic-link",
    response_model=StatusResponse,
    responses={200: {"description": "Always returned, regardless of whether the email is registered"}},
)
async def magic_link(
    payload: MagicLinkRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Step 1 of passwordless sign-in. Emails a one-click login link instead of
    requiring a password.

    Flow:
    1. Client POSTs `{"email": ...}` here.
    2. If that email has an account, a random 32-byte token
       (`secrets.token_urlsafe`) is generated and stored on the user row
       alongside a 15-minute expiry, then queued to the outbox as
       `https://stikkverse.app/magic-login?token=...`.
    3. Response is identical either way -- same anti-enumeration shape as
       `/forgot-password` -- so this endpoint can't be used to test which
       emails are registered.
    4. The frontend page at that URL reads `token` from the query string and
       POSTs it to `/magic-login` (step 2) to actually get signed in.

    This token is a separate column (`magic_link_token`) from the
    password-reset token, so a leaked magic link can only sign someone in --
    it can never be used to change the password.
    """
    result = await db.execute(select(User).where(User.email == payload.email))
    db_user = result.scalars().first()

    if db_user:
        token = secrets.token_urlsafe(32)
        db_user.magic_link_token = token
        db_user.magic_link_token_expires = datetime.utcnow() + timedelta(minutes=15)
        login_link = f"https://stikkverse.app/magic-login?token={token}"
        await queue_email(
            db,
            db_user.email,
            "Your StikkVerse sign-in link",
            f"Use this link to sign in (valid 15 minutes):\n\n{login_link}\n\n"
            "If you didn't request this, you can ignore this email.",
            html=magic_link_email(login_link),
        )
        await db.commit()
        background_tasks.add_task(_dispatch_outbox_now)

    return {"status": "success", "message": "If that email is registered, a sign-in link has been sent."}


@router.post(
    "/magic-login",
    response_model=Token,
    responses={400: {"description": "Magic link token is invalid, already used, or expired"}},
)
async def magic_login(payload: MagicLoginRequest, db: AsyncSession = Depends(get_db)):
    """
    Step 2 of passwordless sign-in. Exchanges the token from the `/magic-link`
    email for a real JWT, exactly like `/login` but with a token instead of a
    password.

    Looks the user up by `magic_link_token`, rejects with 400 if the token
    doesn't match any user or its 15-minute expiry has passed, then clears
    the token before returning the JWT (via the same `_issue_token()` helper
    `/login` uses) -- so the token is single-use: a second request with the
    same value gets a 400 instead of a second valid session.
    """
    result = await db.execute(select(User).where(User.magic_link_token == payload.token))
    db_user = result.scalars().first()

    if (
        not db_user
        or not db_user.magic_link_token_expires
        or db_user.magic_link_token_expires < datetime.utcnow()
    ):
        raise HTTPException(status_code=400, detail="Invalid or expired magic link")

    db_user.magic_link_token = None
    db_user.magic_link_token_expires = None
    await db.commit()

    return await _issue_token(db, db_user)


@router.post("/logout", response_model=LogoutResponse)
async def logout():
    return {"status": "success", "message": "Successfully logged out"}


@router.get(
    "/me",
    response_model=UserProfile,
    responses={401: {"description": "Missing or invalid JWT"}},
)
async def read_users_me(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Mill).where(Mill.user_id == current_user.id))
    mills = result.scalars().all()

    return {
        "id": current_user.id,
        "email": current_user.email,
        "role": current_user.role.value,
        "created_at": current_user.created_at,
        "mills": [
            {
                "mill_id": m.mill_id,
                "api_key": m.api_key,
                "has_baseline": m.has_uploaded_baseline,
            }
            for m in mills
        ],
    }
