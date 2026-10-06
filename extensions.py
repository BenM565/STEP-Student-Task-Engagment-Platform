# Shared Flask extension instances.
# Kept outside app.py so packages such as ai_agents can import db without
# importing app.py (which would create a second app when run as __main__).
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
