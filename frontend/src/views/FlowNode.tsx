import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { memo } from "react";
import { iconFor } from "../lib/icons";
import type { GraphNode } from "../types";

export type FlowNodeType = Node<GraphNode, "flow">;

/** One node on the canvas. Shape depends on the node type (n8n-inspired). */
function FlowNodeComponent({ data, selected }: NodeProps<FlowNodeType>) {
  const { icon: Icon, color } = iconFor(data.icon);
  const shape = `flow-node flow-node--${data.type}${selected ? " is-selected" : ""}`;
  const isAgent = data.type === "agent";
  const isTool = data.type === "tool";

  return (
    <div className={shape} title={data.summary || data.label}>
      {!isTool && <Handle type="target" position={Position.Left} className="handle" />}
      {isTool && <Handle type="target" position={Position.Top} className="handle handle--diamond" />}

      <div className="flow-node__box">
        <Icon size={isAgent ? 26 : 30} color={color} strokeWidth={1.8} />
        {isAgent && <span className="flow-node__inline-label">{data.label}</span>}
      </div>
      {!isAgent && <div className="flow-node__label">{data.label}</div>}
      {data.services.length > 0 && !isAgent && (
        <div className="flow-node__service">{data.services.join(", ")}</div>
      )}

      {!isTool && data.type !== "end" && <Handle type="source" position={Position.Right} className="handle" />}
      {/* Invisible bottom handles so loop back-edges can run underneath the row. */}
      {!isTool && <Handle id="loop-out" type="source" position={Position.Bottom} className="handle handle--hidden" />}
      {data.type === "loop" && <Handle id="loop-in" type="target" position={Position.Bottom} className="handle handle--hidden" />}
      {isAgent && <Handle id="tools" type="source" position={Position.Bottom} className="handle handle--diamond" />}
    </div>
  );
}

export const FlowNode = memo(FlowNodeComponent);
