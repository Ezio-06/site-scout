"""Site Scout - run:  python run_scout.py https://example.com/listing  (same as the site-scout command)"""
import sys

from scout.cli import main

sys.exit(main())
