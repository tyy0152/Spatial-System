from sqlalchemy import text
from database import SessionLocal

def init_database():
    """【服务启动时执行一次】负责检查 PostGIS 扩展、创建表并填充高质量种子数据"""
    db = SessionLocal()
    try:
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

        # 检查表内是否有数据，如果没有则写入学府路等高质量种子数据
        result = db.execute(text("SELECT COUNT(*) FROM pois;")).scalar()
        if result == 0:
            sample_data = [
                ("中央大街步行街", "购物", 126.634, 45.773),
                ("索菲亚教堂广场", "旅游", 126.628, 45.778),
                ("哈尔滨工业大学", "教育", 126.632, 45.742),
                ("太阳岛风景区", "旅游", 126.615, 45.801),
                ("冰雪大世界", "娱乐", 126.650, 45.795),
                ("秋林公司商厦", "购物", 126.643, 45.765),
                ("西西弗书店（学府凯德店）", "咖啡店/书店", 126.6026, 45.7199),
                ("nomore咖啡服装集合点", "服装店/咖啡店", 126.6096, 45.7164),
                ("清滨公园", "公园", 126.6090, 45.7277),
                ("其间（学府花园店）", "咖啡店", 126.6238, 45.7144),
                ("蜜雪冰城（理工大学店）", "饮品店/奶茶店", 126.6194, 45.7136),
                ("大喜芝简餐", "餐食", 126.6214, 45.7127),
                ("哈尔滨理工大学", "教育", 126.6064, 45.7132),
                ("黑龙江大学", "教育", 126.6081, 45.7061),
            ]
            for name, cat, lng, lat_val in sample_data:
                db.execute(
                    text("INSERT INTO pois (name, category, geom) VALUES (:name, :cat, ST_SetSRID(ST_MakePoint(:lng, :lat_val), 4326));"),
                    {"name": name, "cat": cat, "lng": lng, "lat_val": lat_val}
                )
            db.commit()
            print("🚀 PostGIS 数据表及初始种子数据初始化成功！")
    finally:
        db.close()


def execute_spatial_query_internal(lat: float, lon: float, radius: float = 10000):
    db = SessionLocal()
    try:
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