// v1.1 P2 引擎探活 hook：进页拉一次 GET /agent/v1/meta（workflow 引擎下 404 → offline）
import { useEffect, useState } from "react";

import { getAgentMeta } from "../api";
import type { AgentEngineMeta } from "../types";

export type AgentMetaState =
  | { status: "probing" }
  | { status: "online"; meta: AgentEngineMeta }
  | { status: "offline"; message: string };

export function useAgentMeta(): AgentMetaState {
  const [state, setState] = useState<AgentMetaState>({ status: "probing" });

  useEffect(() => {
    let alive = true;
    getAgentMeta()
      .then((meta) => {
        if (alive) setState({ status: "online", meta });
      })
      .catch((error) => {
        if (alive) {
          setState({ status: "offline", message: (error as Error).message || "连接失败" });
        }
      });
    return () => {
      alive = false;
    };
  }, []);

  return state;
}
