import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Model-written text: raw HTML is not rendered (react-markdown's default) and
// links open elsewhere, so nothing a model wrote can act inside the page.
export function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noopener noreferrer nofollow">
              {children}
            </a>
          ),
          img: ({ alt }) => <span className="muted">[图片：{alt ?? ""}]</span>,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
