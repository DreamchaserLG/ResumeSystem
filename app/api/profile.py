"""Personal profile and private, re-encoded profile pictures."""
from io import BytesIO
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Response, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.core.auth import AuthContext, require_auth
from app.core.db import owner_engine, rls_session
from app.core.workflow import audit

router = APIRouter(prefix="/auth/profile", tags=["auth"])
PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
MAX_UPLOAD = 2 * 1024 * 1024
PROFILE_SQL = """
    SELECT u.id AS user_id,u.display_name,u.email,u.created_at,
           coalesce(p.job_title,'') AS job_title,coalesce(p.phone,'') AS phone,
           coalesce(p.office,'') AS office,coalesce(p.bio,'') AS bio,
           p.avatar_webp IS NOT NULL AS has_avatar,
           ARRAY(SELECT d.name FROM department d WHERE d.id=ANY(CAST(:departments AS bigint[]))
                 AND d.status='active' ORDER BY d.name) AS departments
    FROM app_user u LEFT JOIN user_profile p ON p.user_id=u.id WHERE u.id=:id
"""


class EditProfile(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    display_name: str = Field(min_length=1, max_length=80)
    job_title: str = Field(default="", max_length=80)
    phone: str = Field(default="", max_length=32)
    office: str = Field(default="", max_length=80)
    bio: str = Field(default="", max_length=300)


def read_profile(connection, auth):
    profile = connection.execute(text(PROFILE_SQL), {
        "id": auth.user_id, "departments": auth.departments,
    }).mappings().one()
    return {**profile, "role": auth.role}


def avatar_bytes(raw):
    try:
        with Image.open(BytesIO(raw)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("format")
            if not (0 < source.width <= 5000 and 0 < source.height <= 5000) or source.width * source.height > 16_000_000:
                raise ValueError("dimensions")
            image = ImageOps.fit(ImageOps.exif_transpose(source).convert("RGB"), (256, 256), Image.Resampling.LANCZOS)
            result = BytesIO()
            image.save(result, format="WEBP", quality=86)
            return result.getvalue()
    except (ValueError, OSError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, "请选择有效的 JPG、PNG 或 WebP 图片，且最长边不超过 5000 像素。") from exc


@router.get("")
def get_profile(response: Response, auth: AuthContext = Depends(require_auth)):
    response.headers.update(PRIVATE_HEADERS)
    with rls_session(user_id=auth.user_id) as session:
        return read_profile(session, auth)


@router.get("/avatar")
def get_avatar(auth: AuthContext = Depends(require_auth)):
    with rls_session(user_id=auth.user_id) as session:
        data = session.execute(text("SELECT avatar_webp FROM user_profile WHERE user_id=:id"), {"id": auth.user_id}).scalar_one_or_none()
    if data is None:
        raise HTTPException(404, "尚未设置头像")
    return Response(data, media_type="image/webp", headers=PRIVATE_HEADERS)


@router.post("")
def update_profile(
    response: Response, display_name: Annotated[str, Form(max_length=80)],
    job_title: Annotated[str, Form(max_length=80)] = "",
    phone: Annotated[str, Form(max_length=32)] = "",
    office: Annotated[str, Form(max_length=80)] = "",
    bio: Annotated[str, Form(max_length=300)] = "",
    avatar: UploadFile | None = None, remove_avatar: Annotated[bool, Form()] = False,
    auth: AuthContext = Depends(require_auth),
):
    from pydantic import ValidationError

    response.headers.update(PRIVATE_HEADERS)
    try:
        fields = EditProfile(display_name=display_name,job_title=job_title,phone=phone,office=office,bio=bio).model_dump()
    except ValidationError as exc:
        raise HTTPException(422, "请输入显示姓名。") from exc
    if fields["phone"] and not re.fullmatch(r"[0-9+ ()\-]{3,32}", fields["phone"]):
        raise HTTPException(422, "工作电话只能包含数字、空格和 + ( ) -。")
    if avatar and remove_avatar:
        raise HTTPException(422, "请选择上传或移除头像。")
    image = None
    if avatar:
        raw = avatar.file.read(MAX_UPLOAD + 1)
        if len(raw) > MAX_UPLOAD:
            raise HTTPException(413, "头像不能超过 2 MB。")
        image = avatar_bytes(raw)
    with owner_engine.begin() as conn:
        uid = conn.execute(text("""
            UPDATE app_user SET display_name=:display_name
            WHERE id=:id AND status='active' AND perm_version=:permission RETURNING id
        """), {"id":auth.user_id,"permission":auth.perm_version,**fields}).scalar_one_or_none()
        if uid is None:
            raise HTTPException(401, "权限已变更，请重新登录。")
        conn.execute(text("""
            INSERT INTO user_profile(user_id,job_title,phone,office,bio,avatar_webp)
            VALUES(:id,:job_title,:phone,:office,:bio,:avatar)
            ON CONFLICT (user_id) DO UPDATE SET
                job_title=excluded.job_title,phone=excluded.phone,office=excluded.office,bio=excluded.bio,
                avatar_webp=CASE WHEN :remove THEN NULL ELSE coalesce(excluded.avatar_webp,user_profile.avatar_webp) END,
                updated_at=now()
        """), {"id":uid,"avatar":image,"remove":remove_avatar,**fields})
        audit(conn,uid,"profile_update",uid,{"avatar_action":"replace" if image else "remove" if remove_avatar else "keep"},"user")
        return read_profile(conn, auth)
