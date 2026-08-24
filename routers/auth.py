from pydantic import BaseModel

from main import *

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    password: str


@router.post("/login")
def login(body: LoginBody):
    """Misma lógica que Zentrix: una contraseña de admin y otra de equipo."""
    if body.password == ADMIN_PASSWORD:
        role = ROLE_ADMIN
    elif body.password == TEAM_PASSWORD:
        role = ROLE_EDITOR
    else:
        raise HTTPException(401, "Contraseña incorrecta")
    return {"token": create_token(role), "role": role}


@router.get("/me")
def me(role: str = Depends(current_role)):
    return {"role": role}
