"""로컬/컨테이너 운영자 전용: python -m app.manage_dashboard list|grant|revoke."""
import argparse
import asyncio
from uuid import UUID

from sqlalchemy import select

from app.database import AsyncSessionLocal, Base, engine
from app.dashboard_models import DashboardUser
from app.services.dashboard_access import change_access


async def run(args):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        if args.command == "list":
            users = (await db.scalars(select(DashboardUser).order_by(DashboardUser.username))).all()
            for user in users:
                print(f"{user.sub}\t{user.username}\t{user.name}\t{user.role}\t{'enabled' if user.enabled else 'disabled'}")
        else:
            sub = str(UUID(args.sub))
            user = await change_access(db, sub, args.role if args.command == "grant" else "pending",
                                       args.command == "grant", create=True)
            print(f"{sub}: {user['role']}, enabled={user['enabled']}")
    await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    grant = commands.add_parser("grant")
    grant.add_argument("sub")
    grant.add_argument("--role", choices=["viewer", "admin"], required=True)
    revoke = commands.add_parser("revoke")
    revoke.add_argument("sub")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
