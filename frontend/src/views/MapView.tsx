import { hierarchy, treemap, type HierarchyRectangularNode } from "d3-hierarchy";
import { useEffect, useMemo, useRef, useState } from "react";
import { folderGroups } from "../lib/icons";
import { useCurrentGraph, useStore } from "../store";
import type { FileEntry } from "../types";

interface TreeNode {
  name: string;
  path: string;
  file?: FileEntry;
  children?: TreeNode[];
}

/** Turn flat file paths into a folder tree. */
function buildTree(files: FileEntry[], project: string): TreeNode {
  const root: TreeNode = { name: project, path: "", children: [] };
  for (const file of files) {
    const parts = file.path.split("/");
    let current = root;
    parts.forEach((part, i) => {
      const path = parts.slice(0, i + 1).join("/");
      if (i === parts.length - 1) {
        current.children!.push({ name: part, path, file });
        return;
      }
      let next = current.children!.find((c) => c.path === path && c.children);
      if (!next) {
        next = { name: part, path, children: [] };
        current.children!.push(next);
      }
      current = next;
    });
  }
  return root;
}

/** Bird's-eye map: folders are regions, files are tiles sized by lines of code. */
export function MapView() {
  const analysis = useStore((s) => s.analysis);
  const selectedFile = useStore((s) => s.selectedFile);
  const selectFile = useStore((s) => s.selectFile);
  const graph = useCurrentGraph();
  const container = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    if (!container.current) return;
    const observer = new ResizeObserver(([entry]) =>
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height }),
    );
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);

  const groups = useMemo(() => folderGroups(analysis?.files.map((f) => f.path) ?? []), [analysis]);

  const cells = useMemo(() => {
    if (!analysis || size.width === 0) return [];
    const root = hierarchy(buildTree(analysis.files, analysis.project))
      .sum((d) => (d.file ? Math.max(d.file.lines, 1) : 0))
      .sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
    return treemap<TreeNode>()
      .size([size.width, size.height])
      .paddingOuter(3) // must come before paddingTop, which it would otherwise reset
      .paddingTop((d) => (d.depth === 0 ? 3 : 20))
      .paddingInner(2)
      .round(true)(root)
      .descendants()
      .filter((d) => d.depth > 0) as HierarchyRectangularNode<TreeNode>[];
  }, [analysis, size]);

  const workflowFiles = useMemo(() => new Set(graph?.nodes.map((n) => n.file).filter(Boolean)), [graph]);

  return (
    <div ref={container} className="map-view">
      {cells.map((cell) => {
        const width = cell.x1 - cell.x0;
        const height = cell.y1 - cell.y0;
        if (width < 2 || height < 2) return null;
        const style = { left: cell.x0, top: cell.y0, width, height };
        const { file } = cell.data;

        if (!file) {
          return (
            <div key={cell.data.path} className="map-folder" style={style}>
              {width > 50 && <span>{cell.data.name}/</span>}
            </div>
          );
        }
        const classes = [
          "map-file",
          selectedFile === file.path && "is-selected",
          workflowFiles.has(file.path) && "in-workflow",
        ].filter(Boolean).join(" ");
        return (
          <button
            key={file.path}
            className={classes}
            style={{ ...style, background: groups.colorOf(file.path) }}
            title={`${file.path}\n${file.lines} lines${file.summary ? `\n${file.summary}` : ""}`}
            onClick={() => selectFile(file.path)}
          >
            {width > 60 && height > 22 && <span>{file.path.split("/").pop()}</span>}
          </button>
        );
      })}
    </div>
  );
}
