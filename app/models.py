from pydantic import BaseModel, Field
from typing import Literal, Optional, List, Any


class RouteRequest(BaseModel):
    source_lat: float = Field(..., description="Source latitude (WGS84)")
    source_lon: float = Field(..., description="Source longitude (WGS84)")
    dest_lat: float = Field(..., description="Destination latitude (WGS84)")
    dest_lon: float = Field(..., description="Destination longitude (WGS84)")
    algorithm: Literal["dijkstra", "bidirectional", "astar", "highway"] = "astar"


class StepItem(BaseModel):
    type: str          # "node" | "edge"
    direction: str     # "fwd" | "bwd"
    id: Optional[int] = None
    # edge fields
    from_node: Optional[int] = Field(None, alias="from")
    to_node: Optional[int] = Field(None, alias="to")

    class Config:
        populate_by_name = True


class RouteResponse(BaseModel):
    success: bool
    algorithm: str
    distance_m: float
    distance_km: float
    nodes_in_path: int
    nodes_expanded: int
    total_nodes: int
    total_edges: int
    time_ms: float
    # geometry for rendering
    path_coords: List[List[float]]   # [[lat, lon], ...]
    steps: List[Any]                  # exploration steps (raw dicts, compact)
    # node coordinate lookup (only nodes referenced in steps)
    node_coords: dict                 # { node_id_str: [lat, lon] }
    source_node: int
    dest_node: int
    error: Optional[str] = None


class GraphInfoResponse(BaseModel):
    total_nodes: int
    total_edges: int
    graph_file: str
    hh_loaded: bool
