"""定位解析：经纬度 → 医院名称。

只做一次性解析：不写数据库、不保存坐标、不把坐标回传给前端。
坐标由服务端转发给 OpenStreetMap 的 Photon 服务做地址/附近医院查询。
"""

import logging
import math
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import current_user, rate_limit
from app.db.models import User

logger = logging.getLogger("app.geo")
router = APIRouter(prefix="/api/geo", tags=["geo"])

PHOTON = "https://photon.komoot.io"
UA = "xiaolu-medrec/1.0 (hospital lookup)"
HOSPITAL_TYPES = {"hospital", "clinic", "doctors"}
HOSPITAL_NAME = re.compile(r"医院|卫生院|诊所|保健院|医疗中心|hospital|clinic", re.I)
MAX_KM = 2.5


class GeoIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


def _address(props: dict) -> str:
    parts: list[str] = []
    for key in ("state", "city", "district", "locality", "street"):
        value = str(props.get(key) or "").strip()
        if value and value not in parts:
            parts.append(value)
    number = str(props.get("housenumber") or "").strip()
    if number:
        parts.append(number + "号" if number.isdigit() else number)
    return "".join(parts)


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def _photon(client: httpx.AsyncClient, path: str, params: dict) -> dict:
    try:
        resp = await client.get(
            PHOTON + path,
            params=params,
            headers={"User-Agent": UA, "Accept": "application/json"},
        )
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "地址解析暂时不可用") from None
    if resp.status_code >= 400:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "地址解析暂时不可用")
    try:
        return resp.json()
    except ValueError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "地址解析返回异常") from None


@router.post("/hospital", dependencies=[Depends(rate_limit("geo", 20))])
async def reverse_hospital(
    body: GeoIn,
    _user: User = Depends(current_user),
) -> dict:
    """返回 {hospital, address, distance_m}；认不出医院时 hospital 为空。"""
    lat, lon = body.lat, body.lon
    timeout = httpx.Timeout(14.0, connect=8.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        rev = await _photon(client, "/reverse", {"lat": lat, "lon": lon})
        features = rev.get("features") or []
        props = (features[0].get("properties") or {}) if features else {}
        address = _address(props)

        name = str(props.get("name") or "").strip()
        value = str(props.get("osm_value") or "").lower()
        if name and (value in HOSPITAL_TYPES or HOSPITAL_NAME.search(name)):
            return {"hospital": name, "address": address, "distance_m": 0}

        # 最近的地物不是医院：在约 3km 范围内找最近的医院
        pad = 0.03
        bbox = f"{lon - pad:.5f},{lat - pad:.5f},{lon + pad:.5f},{lat + pad:.5f}"
        found = await _photon(
            client, "/api/", {"q": "hospital", "bbox": bbox, "limit": 30, "lang": "default"}
        )
        best = None
        for feature in found.get("features") or []:
            p = feature.get("properties") or {}
            n = str(p.get("name") or "").strip()
            v = str(p.get("osm_value") or "").lower()
            if not n or v not in ("hospital", "clinic"):
                continue
            if "兽医" in n or "宠物" in n or v == "veterinary":
                continue
            coords = (feature.get("geometry") or {}).get("coordinates") or []
            if len(coords) != 2:
                continue
            km = _distance_km(lat, lon, float(coords[1]), float(coords[0]))
            if best is None or km < best[0]:
                best = (km, n)
        if best and best[0] <= MAX_KM:
            return {"hospital": best[1], "address": address, "distance_m": int(best[0] * 1000)}
        return {"hospital": "", "address": address, "distance_m": None}
