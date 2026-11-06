"""Entry point: python -m spark_api.poller [--once]  (see services/poller.py)."""
from spark_api.services.poller import main

if __name__ == "__main__":
    main()
