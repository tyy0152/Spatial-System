from pydantic import BaseModel

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