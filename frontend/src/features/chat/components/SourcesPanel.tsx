// M1 #4 来源引用面板
import { ExternalLink, FileText } from "lucide-react";

import type { SourceRef } from "../types";

export default function SourcesPanel({ sources }: { sources: SourceRef[] }) {
  if (!sources || sources.length === 0) return null;
  return (
    <div className="mt-2">
      <p className="mb-1.5 text-xs font-medium text-muted-foreground">来源引用</p>
      <div className="flex flex-wrap gap-1.5">
        {sources.map((s) => (
          <span
            key={s.index ?? s.docId}
            className="inline-flex max-w-52 items-center gap-1 rounded-md border bg-background px-2 py-1 text-xs text-muted-foreground"
          >
            <FileText className="size-3 shrink-0" />
            <span className="truncate" title={s.docName ?? s.docId}>
              {s.docName ?? s.docId}
            </span>
            {s.url && <ExternalLink className="size-3 shrink-0" />}
          </span>
        ))}
      </div>
    </div>
  );
}
