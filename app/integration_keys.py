"""Provision or revoke an integration bearer credential using the migration role."""
import argparse
import secrets

from sqlalchemy import text

from app.core.db import owner_engine
from app.core.integration import token_digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("rotate", "revoke"))
    parser.add_argument("client_id", type=int)
    args = parser.parse_args()

    with owner_engine.begin() as connection:
        client = connection.execute(text("""
            UPDATE integration_client SET perm_version = perm_version + 1
            WHERE id = :client AND (:revoke OR status = 'active') RETURNING id
        """), {"client": args.client_id, "revoke": args.action == "revoke"}).scalar_one_or_none()
        if client is None:
            parser.error("集成账号不存在或未启用")
        if args.action == "revoke":
            connection.execute(text("DELETE FROM integration_api_key WHERE client_id = :client"), {"client": client})
            token = None
        else:
            token = f"rsint_{secrets.token_urlsafe(32)}"
            connection.execute(text("""
                INSERT INTO integration_api_key (client_id, key_digest) VALUES (:client, :digest)
                ON CONFLICT (client_id) DO UPDATE SET key_digest = EXCLUDED.key_digest, created_at = now()
            """), {"client": client, "digest": token_digest(token)})
        connection.execute(text("""
            INSERT INTO audit_log (action, target_type, target_id) VALUES (:action, 'integration_client', :target)
        """), {"action": f"integration_key_{args.action}", "target": str(client)})
    if token:
        print(token)


if __name__ == "__main__":
    main()
