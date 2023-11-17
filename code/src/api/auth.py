import os
from fastapi import Security, HTTPException
from fastapi.security.api_key import APIKeyHeader
from mana_common.shared import log
from enum import Enum


# Roles supported by this library
class Role(Enum):
    ADMIN = 1
    READ_ONLY = 2


# If no api keys are provided as config for a given role, any user will be granted this role
api_keys_admin = None if not os.environ.get("API_KEYS_ADMIN") else os.environ.get("API_KEYS_ADMIN").split(",")
api_keys_read_only = None if not os.environ.get("API_KEYS_READ_ONLY") \
    else os.environ.get("API_KEYS_READ_ONLY").split(",")

# Key is in header
api_key_header = APIKeyHeader(name="authorization", auto_error=False)


def roles_from_api_key(api_key):
    roles = []
    if api_keys_admin is None or api_key in api_keys_admin:
        roles.append(Role.ADMIN)
    if api_keys_read_only is None or api_key in api_keys_read_only:
        roles.append(Role.READ_ONLY)

    return roles


def any_role(array_of_roles):

    def check_role(api_key: str = Security(api_key_header)):
        roles = roles_from_api_key(api_key)
        log.info("Roles for api_key: {} = {}".format(api_key, roles))
        if len([value for value in roles if value in array_of_roles]) == 0:
            raise HTTPException(status_code=401 if len(roles) == 0 else 403)

    return check_role
