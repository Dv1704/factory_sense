import asyncio
import pandas as pd
from sqlalchemy import select, update, insert
from app.core.database import get_db, AsyncSessionLocal
from app.models.mill_data import MachineDataPoint, MachineBaseline, MachineBaselineHistory
from app.core.analysis import calculate_baseline_stats

async def refine_machine_baseline(user_id: int, mill_id: str, machine_id: str, days: int = 7):
    async with AsyncSessionLocal() as db:
        # 1. Fetch recent data
        print(f"Refining baseline for {mill_id}/{machine_id} over last {days} days...")
        res = await db.execute(
            select(MachineDataPoint).where(
                MachineDataPoint.user_id == user_id,
                MachineDataPoint.mill_id == mill_id,
                MachineDataPoint.machine_id == machine_id
            ).order_by(MachineDataPoint.timestamp.desc())
            # Limit logic can be added here
        )
        points = res.scalars().all()
        
        if not points:
            print("No data found to refine baseline.")
            return

        # 2. Convert to DataFrame
        df = pd.DataFrame([{
            'current_A': p.current_A,
            'motor_state': p.motor_state
        } for p in points])

        # 3. Calculate new stats
        mu, sigma, p95 = calculate_baseline_stats(df)
        
        if mu == 0:
            print("Insufficient 'RUNNING' data to refine baseline.")
            return

        print(f"New Stats: Mu={mu:.2f}, Sigma={sigma:.2f}, P95={p95:.2f} (from {len(df)} points)")

        # 4. Update Baseline
        await db.execute(
            update(MachineBaseline)
            .where(
                MachineBaseline.user_id == user_id,
                MachineBaseline.machine_id == machine_id
            )
            .values(
                mean_current=mu,
                std_current=sigma,
                p95_current=p95,
                data_points_count=len(df)
            )
        )

        # 5. Log History
        await db.execute(
            insert(MachineBaselineHistory).values(
                user_id=user_id,
                mill_id=mill_id,
                machine_id=machine_id,
                mean_current=mu,
                std_current=sigma,
                p95_current=p95,
                data_points_count=len(df),
                update_type="AUTO_REFINEMENT"
            )
        )
        
        await db.commit()
        print("Baseline refined and history updated.")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 4:
        print("Usage: python refine_baseline.py <user_id> <mill_id> <machine_id>")
    else:
        uid = int(sys.argv[1])
        mid = sys.argv[2]
        macid = sys.argv[3]
        asyncio.run(refine_machine_baseline(uid, mid, macid))
