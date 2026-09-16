import sys
import time
from urllib.request import urlopen

from redis import Redis

from chat.config import read_secret


def main() -> int:
    try:
        if sys.argv[1] == "api":
            with urlopen("http://127.0.0.1:8000/health/ready", timeout=3) as response:
                return 0 if response.status == 200 else 1
        with Redis.from_url(read_secret("REDIS_URL"), socket_timeout=2) as redis:
            stamp = redis.get("maintenance:last_success")
            return 0 if stamp and 0 <= time.time() - float(stamp) < 90 else 1
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
