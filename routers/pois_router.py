from fastapi import APIRouter, HTTPException, Query
from models import POIResponse
from services.spatial_service import execute_spatial_query_internal

router = APIRouter(prefix="/api/pois", tags=["POI Spatial Search"])

@router.get("/radius", response_model=list[POIResponse])
def get_pois_by_radius(
    lat: float = Query(..., description="中心点纬度"),
    lon: float = Query(..., description="中心点经度"),
    radius: float = Query(10000, description="查询半径（米）")
):
    result = execute_spatial_query_internal(lat, lon, radius)
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result