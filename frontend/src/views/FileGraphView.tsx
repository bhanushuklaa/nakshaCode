import cytoscape, { type Core } from "cytoscape";
import { useEffect, useMemo, useRef, useState } from "react";
import { folderGroups, type FolderGroups } from "../lib/icons";
import { useCurrentGraph, useStore } from "../store";

/** Every parsed file as a node; edges are imports. Click a file to inspect it. */
export function FileGraphView() {
  const container = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  const analysis = useStore((s) => s.analysis);
  const selectedFile = useStore((s) => s.selectedFile);
  const selectFile = useStore((s) => s.selectFile);
  const graph = useCurrentGraph();
  const [hideIsolated, setHideIsolated] = useState(true);

  // Which files to draw, and the import edges between them.
  const { files, edges, isolatedCount } = useMemo(() => {
    const parsed = (analysis?.files ?? []).filter((f) => f.parsed);
    const known = new Set(parsed.map((f) => f.path));
    const edges = parsed.flatMap((f) => f.imports.filter((t) => known.has(t)).map((t) => [f.path, t] as const));
    const connected = new Set(edges.flat());
    const shown = hideIsolated ? parsed.filter((f) => connected.has(f.path)) : parsed;
    return { files: shown, edges, isolatedCount: parsed.length - connected.size };
  }, [analysis, hideIsolated]);

  const groups = useMemo(() => folderGroups(files.map((f) => f.path)), [files]);

  // Build the graph whenever the set of files changes.
  useEffect(() => {
    if (!container.current || !analysis) return;
    const maxLines = Math.max(1, ...files.map((f) => f.lines));
    const shown = new Set(files.map((f) => f.path));

    const instance = cytoscape({
      container: container.current,
      elements: [
        ...files.map((f) => ({
          data: {
            id: f.path,
            label: f.path.split("/").pop(),
            color: groups.colorOf(f.path),
            size: 16 + 34 * Math.sqrt(f.lines / maxLines),
          },
        })),
        ...edges
          .filter(([source, target]) => shown.has(source) && shown.has(target))
          .map(([source, target]) => ({ data: { id: `${source}->${target}`, source, target } })),
      ],
      style: [
        {
          selector: "node",
          style: {
            "background-color": "data(color)", width: "data(size)", height: "data(size)",
            label: "data(label)", "font-size": 10, color: "#334155", "text-valign": "bottom",
            "text-margin-y": 4, "font-family": "Inter, system-ui, sans-serif",
            "text-max-width": "110px", "text-wrap": "ellipsis",
            "text-background-color": "#ffffff", "text-background-opacity": 0.85, "text-background-padding": "1px",
            "min-zoomed-font-size": 8, // hide labels when zoomed out instead of overlapping them
            "border-width": 2, "border-color": "#fff",
          },
        },
        {
          selector: "edge",
          style: {
            width: 1, "line-color": "#d4d4d8", "target-arrow-color": "#d4d4d8",
            "target-arrow-shape": "triangle", "arrow-scale": 0.6, "curve-style": "bezier", opacity: 0.8,
          },
        },
        { selector: ".faded", style: { opacity: 0.12 } },
        { selector: "node.in-workflow", style: { "border-color": "#4f46e5", "border-width": 3 } },
        { selector: "edge.highlight", style: { "line-color": "#6366f1", "target-arrow-color": "#6366f1", width: 2, opacity: 1 } },
        { selector: "node.neighbor", style: { "min-zoomed-font-size": 0 } },
        {
          selector: "node.selected-file",
          style: { "border-color": "#0f172a", "border-width": 4, "font-weight": "bold", "min-zoomed-font-size": 0 },
        },
      ],
      layout: files.length > 1500
        ? { name: "grid", avoidOverlap: true }
        : {
            name: "cose",
            animate: false,
            randomize: true,
            nodeDimensionsIncludeLabels: true, // keep labels from overlapping neighbours
            nodeRepulsion: () => 16000,
            idealEdgeLength: () => 90,
            edgeElasticity: () => 90,
            gravity: 0.3,
            componentSpacing: 120,
            numIter: 2500,
            padding: 40,
          },
    });

    instance.on("tap", "node", (event) => selectFile(event.target.id()));
    instance.on("tap", (event) => { if (event.target === instance) selectFile(null); });
    cy.current = instance;
    return () => { instance.destroy(); cy.current = null; };
  }, [analysis, files, edges, groups, selectFile]);

  // Highlight the selected file's neighbourhood and the current workflow's files.
  useEffect(() => {
    const instance = cy.current;
    if (!instance) return;
    const workflowFiles = new Set(graph?.nodes.map((n) => n.file).filter(Boolean));
    instance.batch(() => {
      instance.elements().removeClass("faded highlight in-workflow selected-file neighbor");
      instance.nodes().forEach((n) => { if (workflowFiles.has(n.id())) n.addClass("in-workflow"); });
      if (selectedFile) {
        const node = instance.getElementById(selectedFile);
        if (node.nonempty()) {
          const neighborhood = node.closedNeighborhood();
          instance.elements().not(neighborhood).addClass("faded");
          neighborhood.edges().addClass("highlight");
          neighborhood.nodes().addClass("neighbor");
          node.addClass("selected-file");
        }
      }
    });
  }, [selectedFile, graph, files]);

  return (
    <div className="graph-view">
      <div ref={container} className="graph-view__canvas" />
      <div className="graph-toolbar">
        <span>{files.length} files · {edges.length} imports</span>
        {isolatedCount > 0 && (
          <label className="toggle">
            <input type="checkbox" checked={hideIsolated} onChange={() => setHideIsolated((v) => !v)} />
            <span className="toggle__track"><span className="toggle__thumb" /></span>
            Hide {isolatedCount} unconnected files
          </label>
        )}
      </div>
      <Legend groups={groups} />
    </div>
  );
}

function Legend({ groups }: { groups: FolderGroups }) {
  return (
    <div className="legend">
      {groups.names.slice(0, 8).map((name) => (
        <span key={name}>
          <i style={{ background: groups.colorOfGroup(name) }} />
          {name}
        </span>
      ))}
      <span className="legend__hint">Ring = used by the selected workflow · scroll to zoom</span>
    </div>
  );
}
