import pandas as pd
from typing import Tuple, List, Dict
from fastapi import HTTPException
import logging

logger = logging.getLogger(__name__)

def validate_and_clean_csv(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    errors = []
    
    if df.empty:
        return df, ["File has no data rows."]
    
    required_cols = {'timestamp', 'mill_id', 'machine_id', 'current_A', 'motor_state'}
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        raise HTTPException(status_code=400, detail=f"Missing columns: {', '.join(missing_cols)}")

    # Check for complete emptiness in essential columns
    initial_len = len(df)
    df = df.dropna(subset=['timestamp', 'machine_id', 'mill_id'])
    dropped_rows = initial_len - len(df)
    if dropped_rows > 0:
        errors.append(f"Dropped {dropped_rows} rows missing essential timestamp, mill_id, or machine_id.")

    # Validate numeric values
    df['current_A'] = pd.to_numeric(df['current_A'], errors='coerce')
    nan_currents = df['current_A'].isna().sum()
    if nan_currents > 0:
        errors.append(f"Found {nan_currents} rows with non-numeric or missing current_A values. Coercing to 0.0.")
    df['current_A'] = df['current_A'].fillna(0.0)

    # Validate motor_state
    df['motor_state'] = df['motor_state'].astype(str).str.upper().str.strip()
    valid_states = {'RUNNING', 'STOPPED', 'IDLE'}
    invalid_states = df[~df['motor_state'].isin(valid_states)]
    if not invalid_states.empty:
        errors.append(f"Found {len(invalid_states)} rows with unknown motor_states. Defaulting to 'STOPPED'.")
        df.loc[~df['motor_state'].isin(valid_states), 'motor_state'] = 'STOPPED'

    return df, errors

