from fastapi import APIRouter, HTTPException
from openai import OpenAI
import json

from config import DEEPSEEK_API_KEY
from models import ChatRequest
from services.osm_service import geocode_place_with_osm
from services.spatial_service import execute_spatial_query_internal

router = APIRouter(prefix="/api", tags=["AI Agent"])

client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

#多维意图工具定义 (Tool Schema)
tools = [{
    "type": "function",
    "function": {
        "name": "search_pois_advanced",
        "description": "根据用户的多维自然语言意图，精准解析并检索符合条件的兴趣点及周边生活圈。",
        "parameters": {
            "type": "object",
            "properties": {
                "place_name": {
                    "type": "string",
                    "description": "目标核心地名（如：'哈理工'、'中央大街'）。若用户未指定则留空。"
                },
                "radius": {
                    "type": "number",
                    "description": "查询半径，单位为米。例如用户说‘两三公里’或‘3公里’，则转换为 3000。默认 10000。"
                },
                "category": {
                    "type": "string",
                    "description": "目标业态或检索目的。例如：'餐饮'、'咖啡店'、'公园'、'书店'。若用户未指定则留空。"
                },
                "dietary_preference": {
                    "type": "string",
                    "description": "饮食偏好、忌口或健康要求。例如：'清淡'、'不要太油'、'低脂'。若用户未指定则留空。"
                },
                "activity_requirement": {
                    "type": "string",
                    "description": "同行的附加活动需求。例如：'逛街'、'散步'、'看书'。若用户未指定则留空。"
                }
            },
            "required": []
        }
    }
}]

@router.post("/chat")
def chat_with_spatial_agent(req: ChatRequest):
    # 如果前端传了坐标就用前端的，没传就默认哈工大
    default_lat = req.lat if req.lat is not None else 45.742
    default_lon = req.lon if req.lon is not None else 126.632

    messages = [
        {
            "role": "system",
            "content": (
                f"你是一个专业、干练的哈尔滨本地空间智能导游。\n"
                f"【空间与语义仲裁规则】\n"
                f"1. 地图当前锁定的中心坐标是：纬度 {default_lat}, 经度 {default_lon}。\n"
                f"2. 若用户文本明确指定了其他特定地名（如“xxx学校”、“xx商场”），优先以文本地点为中心调用工具；否则严格使用地图当前坐标。\n"
                f"【多维意图处理规则】\n"
                f"1. 当用户有输入地址、查询周边、寻找场所、打听路线等需求时，请调用 search_pois_advanced 工具，并将用户输入的地点、半径、类别、口味/健康偏好以及附加活动需求准确提取并填入参数中。\n"
                f"2. 拿到数据库返回的 POI 列表后，结合用户的附加偏好（如口味偏好、活动要求、环境要求等），在回复中进行个性化、有逻辑的智能组装推荐。\n"
                f"【输出规范（严格遵守）】\n"
                f"1. 每次对话最多调用一次工具，绝对禁止输出任何 XML、DSML 或函数调用代码标记。\n"
                f"2. 语言简洁热情，如果没有查询到对应场所，直接说明，不要自我剖析定位误差。\n"
                f"3. 开头用一句话简明点出本次检索的基准位置（地图选点 或 文本指定地点）以及对应经纬度即可。"
            )
        },
        {"role": "user", "content": req.message}
    ]

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            tools=tools
        )

        response_message = response.choices[0].message
        messages.append(response_message)

        pois_data = []
        actual_lat = default_lat
        actual_lon = default_lon
        location_source = "地图视窗坐标"
        
        # 缓存解析出来的用户偏好，以便传回给大模型做二次加工
        user_preferences = {}

        if response_message.tool_calls:
            for tool_call in response_message.tool_calls:
                if tool_call.function.name == "search_pois_advanced":
                    args = json.loads(tool_call.function.arguments or "{}")
                    place_name = args.get("place_name", "").strip()
                    radius = args.get("radius", 10000)
                    
                    # 记录用户的附加偏好
                    user_preferences = {
                        "category": args.get("category", ""),
                        "dietary_preference": args.get("dietary_preference", ""),
                        "activity_requirement": args.get("activity_requirement", "")
                    }

                    target_lat = default_lat
                    target_lon = default_lon
                    location_source = "地图视窗坐标"

                    if place_name:
                        osm_lat, osm_lon = geocode_place_with_osm(place_name)
                        if osm_lat is not None and osm_lon is not None:
                            target_lat = osm_lat
                            target_lon = osm_lon
                            location_source = f"OSM 地理编码：{place_name}"
                        else:
                            location_source = "未在OSM匹配到地名，回退至地图坐标"

                    actual_lat = target_lat
                    actual_lon = target_lon
                    
                    # 执行 PostGIS 空间检索
                    api_result = execute_spatial_query_internal(lat=target_lat, lon=target_lon, radius=radius)
                    
                    if not api_result or (isinstance(api_result, list) and len(api_result) == 0):
                        api_result = execute_spatial_query_internal(lat=target_lat, lon=target_lon, radius=20000)

                    pois_data = api_result if isinstance(api_result, list) else []

                    # 将查询结果回传给大模型
                    messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": "search_pois_advanced",
                        "content": json.dumps(api_result, ensure_ascii=False)
                    })

            # 第二次请求大模型：将多维偏好与真实 POI 列表结合生成最终答复
            second_response = client.chat.completions.create(
                model="deepseek-chat",
                messages=messages
            )
            ai_reply = second_response.choices[0].message.content or ""
            
            if "<｜DSML｜" in ai_reply or "<|DSML|" in ai_reply:
                ai_reply = ai_reply.split("<｜DSML｜")[0].split("<|DSML|")[0].strip()

        else:
            ai_reply = response_message.content

        pois_summary = [f"- {p['name']} ({p['category']}): 约 {p['distance_meters']}米" for p in pois_data]

        return {
            "reply": ai_reply, 
            "pois": pois_summary,
            "active_lat": actual_lat,
            "active_lon": actual_lon,
            "location_source": location_source
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent 运行出错: {str(e)}")