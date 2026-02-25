from app import app, db, User
from werkzeug.security import generate_password_hash

with app.app_context():
    users = User.query.all()
    for u in users:
        if not u.password:
            u.password = generate_password_hash("password123")
    db.session.commit()

print("Passwords fixed successfully")
