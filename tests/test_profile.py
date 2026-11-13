"""Profile updates persist across sessions and stay scoped to the current account."""
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app


def sign_in(client, email, password):
    response = client.post("/api/v1/auth/login",json={"email":email,"password":password})
    assert response.status_code == 200
    return {"X-Session-Token":response.json()["token"]}


def test_profile_fields_and_avatar_are_private_and_persistent():
    with TestClient(app) as client:
        hr = sign_in(client,"hr.tech@company.internal","hr123456")
        other = sign_in(client,"hr.market@company.internal","dev123456")
        path = "/api/v1/auth/profile"
        assert client.get(path).status_code == 401
        prior = client.get(path,headers=hr).json()
        image = BytesIO()
        Image.new("RGB",(220,120),"steelblue").save(image,format="PNG")
        try:
            edited = client.post(path,headers=hr,data={
                "display_name":"  测试职员  ","job_title":"招聘经理","phone":"+86 12345678","office":"会议楼","bio":"内部招聘",
                "role":"super_admin","email":"foreign@example.test","user_id":999,
            },files={"avatar":("profile.png",image.getvalue(),"image/png")})
            assert edited.status_code == 200, edited.text
            updated = edited.json()
            assert (updated["display_name"],updated["job_title"],updated["office"]) == ("测试职员","招聘经理","会议楼")
            assert updated["role"] == prior["role"] and updated["email"] == prior["email"]
            assert client.get("/api/v1/auth/me",headers=hr).json()["display_name"] == "测试职员"
            fresh = sign_in(client,"hr.tech@company.internal","hr123456")
            assert client.get(path,headers=fresh).json() == updated
            picture = client.get(path+"/avatar",headers=fresh)
            assert picture.headers["content-type"] == "image/webp" and "no-store" in picture.headers["cache-control"]
            assert Image.open(BytesIO(picture.content)).size == (256,256)
            assert client.get(path+"/avatar",headers=other).status_code == 404
            assert client.get(path,headers=other).json()["email"] == "hr.market@company.internal"
            invalid = client.post(path,headers=hr,data={"display_name":"保持不变"},
                                  files={"avatar":("invalid.png",b"not an image","image/png")})
            assert invalid.status_code == 422 and client.get(path,headers=hr).json()["display_name"] == "测试职员"
        finally:
            restored = client.post(path,headers=hr,data={
                **{key:prior[key] for key in ("display_name","job_title","phone","office","bio")},
                "remove_avatar":"true",
            })
            assert restored.status_code == 200, restored.text
            assert client.get(path+"/avatar",headers=hr).status_code == 404
