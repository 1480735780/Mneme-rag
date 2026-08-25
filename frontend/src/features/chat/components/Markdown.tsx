// M1 #4 Markdown 安全渲染（react-markdown + remark-gfm + rehype-sanitize）
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

interface Props {
  content: string;
}

export function Markdown({ content }: Props) {
  return (
    <div className="md-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
