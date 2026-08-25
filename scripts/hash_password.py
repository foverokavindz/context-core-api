from app.core.security import hash_password

password = input("Enter password to hash: ")
hashed_password = hash_password(password)
print(f"Original password: {password}")
print(f"Hashed password: {hashed_password}")