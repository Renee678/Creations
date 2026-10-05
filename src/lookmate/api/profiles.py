from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..schemas import ProfileIn, ProfileOut
from ..services.body import bmi, guide_for

router = APIRouter(prefix="/api/users", tags=["profiles"])


def to_out(u: User) -> ProfileOut:
    return ProfileOut(
        id=u.id, nickname=u.nickname, height_cm=u.height_cm, weight_kg=u.weight_kg, age=u.age,
        body_shape=u.body_shape, preferred_styles=u.preferred_styles, budget_per_item=u.budget_per_item,
        bmi=bmi(u.height_cm, u.weight_kg), fit_advice=guide_for(u.body_shape).summary,
    )


def get_user_or_404(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user not found")
    return user


@router.post("", status_code=201)
def create_profile(body: ProfileIn, db: Session = Depends(get_db)) -> ProfileOut:
    user = User(**body.model_dump())
    db.add(user)
    db.commit()
    return to_out(user)


@router.get("/{user_id}")
def read_profile(user_id: int, db: Session = Depends(get_db)) -> ProfileOut:
    return to_out(get_user_or_404(db, user_id))


@router.put("/{user_id}")
def update_profile(user_id: int, body: ProfileIn, db: Session = Depends(get_db)) -> ProfileOut:
    user = get_user_or_404(db, user_id)
    for k, v in body.model_dump().items():
        setattr(user, k, v)
    db.commit()
    return to_out(user)
