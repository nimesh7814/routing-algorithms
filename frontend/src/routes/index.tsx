import { createFileRoute } from "@tanstack/react-router";
import { PathfindingVisualizer } from "@/components/PathfindingVisualizer";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Navigation Algorithms" },
      { name: "description", content: "Apple-inspired navigation algorithm visualizer. Pick A*, Dijkstra, or BFS and watch the search animate across the road network." },
      { property: "og:title", content: "Navigation Algorithms" },
      { property: "og:description", content: "Animated navigation algorithms — A*, Dijkstra, and BFS — over real road networks." },
    ],
  }),
  component: Index,
});

function Index() {
  return (
    <div className="h-screen w-screen overflow-hidden bg-background">
      <PathfindingVisualizer />
    </div>
  );
}
