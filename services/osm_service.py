import requests

def geocode_place_with_osm(place_name: str):
    """
    调用 OSM (Nominatim) 权威接口将地名文本转为精确经纬度，
    彻底根除大模型坐标幻觉。
    """
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": f"哈尔滨 {place_name}",  # 限制在哈尔滨市范围内检索
        "format": "json",
        "limit": 1
    }
    headers = {
        "User-Agent": "HarbinGeoAIAgent/1.0"
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=5)
        data = resp.json()
        if data and len(data) > 0:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        print(f"⚠️ OSM 地理编码请求失败: {e}")
    return None, None