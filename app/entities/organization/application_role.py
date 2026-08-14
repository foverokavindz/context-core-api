from enum import Enum


class ApplicationRole(str, Enum):
    
    SUPER_ADMIN = "SUPER_ADMIN"
    HR = "HR"
    EMPLOYEE = "EMPLOYEE"
