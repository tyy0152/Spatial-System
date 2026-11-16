from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from openai import OpenAI
import requests
import json

# 1. 初始化 FastAPI 实例
app = FastAPI(title="城市空间兴趣点智能检索与 Geo-AI 智能体系统")

# 2. 挂载静态文件目录用于存放前端页面
app.mount("/static", StaticFiles(directory="static"), name="static")

# 3. 数据库连接配置
DATABASE_URL = "xxx"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 4. 初始化大模型客户端 (DeepSeek)
client = OpenAI(
    api_key="xxx",  # 大模型 API Key
    base_url="https://api.deepseek.com"
)

# --- 数据模型定义 ---
class POIResponse(BaseModel):
    id: int
    name: str
    category: str
    longitude: float
    latitude: float
    distance_meters: float

class ChatRequest(BaseModel):
    message: str
    lat: float | None = None
    lon: float | None = None

# --- 新增：调用 OSM 权威接口将地名文本转为精确经纬度（彻底根除大模型坐标幻觉） ---
def geocode_place_with_osm(place_name: str):
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

# --- 核心空间检索底层函数 (也就是 Agent 可以调用的 Tool) ---
def execute_spatial_query_internal(lat: float, lon: float, radius: float = 10000):
    db = SessionLocal()
    try:
        # 确保表存在并初始化种子数据
        db.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS pois (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100),
                category VARCHAR(50),
                geom GEOMETRY(Point, 4326)
            );
        """))
        db.commit()

        result = db.execute(text("SELECT COUNT(*) FROM pois;")).scalar()
        if result == 0:
            sample_data = [
                ("中央大街步行街", "购物", 126.634, 45.773),
                ("索菲亚教堂广场", "旅游", 126.628, 45.778),
                ("哈尔滨工业大学", "教育", 126.632, 45.742),
                ("太阳岛风景区", "旅游", 126.615, 45.801),
                ("冰雪大世界", "娱乐", 126.650, 45.795),
                ("秋林公司商厦", "购物", 126.643, 45.765),
            ]
            for name, cat, lng, lat_val in sample_data:
                db.execute(
                    text("INSERT INTO pois (name, category, geom) VALUES (:name, :cat, ST_SetSRID(ST_MakePoint(:lng, :lat_val), 4326));"),
                    {"name": name, "cat": cat, "lng": lng, "lat_val": lat_val}
                )
            db.commit()

        # 执行 PostGIS 空间球面距离检索
        query = text("""
            SELECT 
                id, name, category, 
                ST_X(geom::geometry) as longitude, 
                ST_Y(geom::geometry) as latitude,
                ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography) as distance_meters
            FROM pois
            WHERE ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :radius)
            ORDER BY distance_meters ASC;
        """)
        rows = db.execute(query, {"lon": lon, "lat": lat, "radius": radius}).fetchall()
        
        results = []
        for row in rows:
            results.append({
                "id": row[0],
                "name": row[1],
                "category": row[2],
                "longitude": row[3],
                "latitude": row[4],
                "distance_meters": round(row[5], 2)
            })
        return results
    except Exception as e:
        return {"error": str(e)}
    finally:
        db.close()


# --- 定义大模型可以调用的 Tools 清单（只提取地名关键词，不让大模型盲猜坐标） ---
tools = [{
    "type": "function",
    "function": {
        "name": "search_pois_by_radius",
        "description": "检索指定地点或当前地图视窗周边的兴趣点。如果用户文本中明确提到了某个特定地名（如‘哈理工’、‘中央大街’），请提取该地名填入 place_name 参数；如果未指定地名，则留空。",
        "parameters": {
            "type": "object",
            "properties": {
                "place_name": {
                    "type": "string",
                    "description": "用户文本中提到的目标地名关键词（如：'哈理工'、'群力'）。若未提及则不填。"
                },
                "radius": {
                    "type": "number",
                    "description": "查询半径，单位为米，默认 10000 米"
                }
            },
            "required": []
        }
    }
}]


# --- 核心 AI Agent 智能体路由 (融合了 Function Calling) ---
@app.post("/api/chat")
def chat_with_spatial_agent(req: ChatRequest):
    # 如果前端传了坐标就用前端的，没传就默认哈工大
    default_lat = req.lat if req.lat is not None else 45.742
    default_lon = req.lon if req.lon is not None else 126.632

    # 构造对话历史
    #messages = [
    #    {"role": "system", "content": f"你是一个资深的哈尔滨本地空间智能导游。当前用户的中心坐标是：纬度 {default_lat}, 经度 {default_lon}。如果用户需要查询周边地点，请务必调用 search_pois_by_radius 工具。"},
    #    {"role": "user", "content": req.message}
    #]
    # 【修改点 2】：引入空间与语义仲裁规则的 System Prompt
    messages = [
        {
            "role": "system",
            "content": (
                f"你是一个专业、干练的哈尔滨本地空间智能导游。\n"
                f"【空间与语义仲裁规则】\n"
                f"1. 地图当前锁定的中心坐标是：纬度 {default_lat}, 经度 {default_lon}。\n"
                f"2. 若用户文本明确指定了其他特定地名（如“哈理工”、“群力”），优先以文本地点为中心调用工具；否则严格使用地图当前坐标。\n"
                f"【输出规范（严格遵守）】\n"
                f"1. 每次对话最多调用一次 search_pois_by_radius 工具，绝对禁止输出任何 XML、DSML 或函数调用代码标记。\n"
                f"2. 拿到数据库返回的 POI 列表后，直接按距离由近到远向用户推荐，语言简洁热情，如果没有查询到对应场所，可以回复“没查到某类场所”，不要自我剖析定位误差。\n"
                f"3. 开头用一句话简明点出本次检索的基准位置（地图选点 或 文本指定地点）即可。"
            )
        },
        {"role": "user", "content": req.message}
    ]

    try:
        # 第一次请求大模型：判断是否需要调用工具
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            tools=tools
        )

        response_message = response.choices[0].message
        messages.append(response_message)

        pois_data = []
        # 【修改点 3】：定义用于追踪实际生效坐标和来源的变量
        actual_lat = default_lat
        actual_lon = default_lon
        location_source = "地图视窗坐标"

        # 检查大模型是否触发了工具调用
        if response_message.tool_calls:
            for tool_call in response_message.tool_calls:
                if tool_call.function.name == "search_pois_by_radius":
                    args = json.loads(tool_call.function.arguments or "{}")
                    place_name = args.get("place_name", "").strip()
                    radius = args.get("radius", 10000)

                    # 默认先使用前端传来的地图视窗坐标
                    target_lat = default_lat
                    target_lon = default_lon
                    location_source = "地图视窗坐标"

                    # 如果大模型从文本中提取出了地名，交由 OSM 地理编码获取真实准确的经纬度！
                    if place_name:
                        osm_lat, osm_lon = geocode_place_with_osm(place_name)
                        if osm_lat is not None and osm_lon is not None:
                            target_lat = osm_lat
                            target_lon = osm_lon
                            location_source = f"OSM 地理编码：{place_name}"
                            print(f"✅ OSM 成功解析地名 [{place_name}] -> 纬度: {target_lat}, 经度: {target_lon}")
                        else:
                            location_source = "未在OSM匹配到地名，回退至地图坐标"

                    actual_lat = target_lat
                    actual_lon = target_lon
                    
                    # 第一次尝试检索# 使用权威且真实的 target_lat, target_lon 进入 PostGIS 计算
                    api_result = execute_spatial_query_internal(lat=target_lat, lon=target_lon, radius=radius)
                    
                    # 💡 【核心兜底代码】如果默认半径（比如 3km 或 10km）内没查到数据，自动帮你扩大到 20 公里再查一次！
                    if not api_result or (isinstance(api_result, list) and len(api_result) == 0):
                        print(f"⚠️ 当前半径 {radius}米 内未检索到 POI，自动触发兜底机制：扩大至 20000米 重新检索...")
                        api_result = execute_spatial_query_internal(lat=target_lat, lon=target_lon, radius=20000)
                        

                    pois_data = api_result if isinstance(api_result, list) else []

                    # 将最终结果（无论是查到的还是兜底扩大的）回传给大模型
                    messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": "search_pois_by_radius",
                        "content": json.dumps(api_result, ensure_ascii=False)
                    })

            # 第二次请求大模型生成最终回答（不传 tools，强制纯文本输出）
            second_response = client.chat.completions.create(
                model="deepseek-chat",
                messages=messages
            )
            ai_reply = second_response.choices[0].message.content or ""
            
            # 【防泄漏清洗】万一模型吐出底层 DSML 标记，直接截断清理
            if "<｜DSML｜" in ai_reply or "<|DSML|" in ai_reply:
                ai_reply = ai_reply.split("<｜DSML｜")[0].split("<|DSML|")[0].strip()

        else:
            ai_reply = response_message.content

        # 提取简要 POI 文本摘要供前端参考
        pois_summary = [f"- {p['name']} ({p['category']}): 约 {p['distance_meters']}米" for p in pois_data]

        #return {"reply": ai_reply, "pois": pois_summary}

        # 在返回的 JSON 中增加 active_lat, active_lon, location_source 供前端小标签使用
        return {
            "reply": ai_reply, 
            "pois": pois_summary,
            "active_lat": actual_lat,
            "active_lon": actual_lon,
            "location_source": location_source
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent 运行出错: {str(e)}")


# --- 基础地图 POI 空间圆域检索接口 (供 Tool 直接调用或独立测试) ---
@app.get("/api/pois/radius", response_model=list[POIResponse])
def get_pois_by_radius(
    lat: float = Query(..., description="中心点纬度"),
    lon: float = Query(..., description="中心点经度"),
    radius: float = Query(10000, description="查询半径（米）")
):
    result = execute_spatial_query_internal(lat, lon, radius)
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result