import { Fragment, type ReactNode } from "react";

/**
 * Minimal markdown renderer for model output.
 *
 * Deliberately not a library. The model emits **bold**, `code`, bullets,
 * headings and paragraphs — that's the whole surface — and a full parser here
 * would mean shipping a markdown engine plus a sanitiser to render text we
 * already control the prompt for.
 *
 * Critically this NEVER uses dangerouslySetInnerHTML. Every output is built as
 * React elements, so text arriving from a model (or, later, from a headline in
 * a feed) cannot inject markup into this app.
 */

/** Inline: **bold**, *italic*, `code`. */
function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    const key = `${keyBase}-${i++}`;
    if (tok.startsWith("**")) {
      out.push(<strong key={key}>{tok.slice(2, -2)}</strong>);
    } else if (tok.startsWith("`")) {
      out.push(<code key={key}>{tok.slice(1, -1)}</code>);
    } else {
      out.push(<em key={key}>{tok.slice(1, -1)}</em>);
    }
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ text }: { text: string }) {
  if (!text) return null;

  const blocks: ReactNode[] = [];
  const lines = text.split("\n");
  let list: string[] = [];
  let para: string[] = [];
  let k = 0;

  const flushList = () => {
    if (!list.length) return;
    blocks.push(
      <ul className="md__ul" key={`ul-${k++}`}>
        {list.map((li, i) => (
          <li key={i}>{inline(li, `li-${k}-${i}`)}</li>
        ))}
      </ul>
    );
    list = [];
  };
  const flushPara = () => {
    if (!para.length) return;
    const body = para.join(" ");
    blocks.push(
      <p className="md__p" key={`p-${k++}`}>
        {inline(body, `p-${k}`)}
      </p>
    );
    para = [];
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      flushList();
      flushPara();
      continue;
    }
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    const heading = line.match(/^\s*(#{1,4})\s+(.*)$/);
    if (bullet) {
      flushPara();
      list.push(bullet[1]);
    } else if (heading) {
      flushList();
      flushPara();
      blocks.push(
        <h4 className="md__h" key={`h-${k++}`}>
          {inline(heading[2], `h-${k}`)}
        </h4>
      );
    } else {
      flushList();
      para.push(line.trim());
    }
  }
  flushList();
  flushPara();

  return <div className="md">{blocks.map((b, i) => <Fragment key={i}>{b}</Fragment>)}</div>;
}
