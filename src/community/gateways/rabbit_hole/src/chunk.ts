// SYNTH:
//   purpose: Header-aware chunking that splits file content into embeddable chunks while preserving document structure.
//   axioms: [evidence_over_intuition, iteration_is_progress, reversibility_awareness]
//   objective: Every file produces deterministic, metadata-rich chunks suitable for Vectorize upsert with header context preserved.
//   anti_patterns:
//     - Non-deterministic vector IDs (IDs must be library:sha256:chunkIndex)
//     - Dropping header context from chunk text (the header must travel with the body)
//     - Chunks exceeding maxChunks limit (must stop at the cap)
//     - Losing content at section boundaries (overlap must prevent gaps)
//     - Importing from proprietary_core

/**
 * VDS 60000 — Rabbit Hole chunking logic.
 *
 * Splits file content into chunks (~800 tokens, 100-token overlap) suitable
 * for embedding. Each chunk carries metadata for Vectorize.
 *
 * Adapted from the sovereign-context7 reference worker pattern.
 */

export interface Chunk {
  id: string;
  text: string;
  metadata: {
    path: string;
    library: string;
    filename: string;
    header: string;
    chunk_index: number;
    sha256: string;
    indexed_at: number;
  };
}

export interface ChunkOptions {
  path: string;
  library: string;
  sha256: string;
  maxChunks?: number;
  targetTokens?: number;
  overlapTokens?: number;
}

const DEFAULT_MAX_CHUNKS = 64;
const DEFAULT_TARGET_TOKENS = 800;
const DEFAULT_OVERLAP_TOKENS = 100;

/**
 * Rough token estimate: ~4 chars per token for English/code.
 * Not precise but sufficient for chunk sizing.
 */
export function estimateTokens(text: string): number {
  return Math.ceil(text.length / 4);
}

/**
 * Extract filename from path (last segment).
 */
export function filenameFromPath(path: string): string {
  const parts = path.replace(/\\/g, "/").split("/");
  return parts[parts.length - 1] || path;
}

/**
 * Build a deterministic vector ID.
 * Format: library:sha256:chunkIndex
 * Vectorize max ID length is 64 bytes; sha256 is truncated to 16 hex chars.
 */
export function vectorId(library: string, sha256: string, chunkIndex: number): string {
  return `${library}:${sha256.slice(0, 16)}:${chunkIndex}`;
}

/**
 * Chunk markdown by headers (## / ###), with fallback to fixed windows.
 * Preserves document structure: each chunk includes its section header.
 * Sub-splits large sections by token windows with overlap.
 */
export function chunkMarkdown(text: string, opts: ChunkOptions): Chunk[] {
  const {
    path,
    library,
    sha256,
    maxChunks = DEFAULT_MAX_CHUNKS,
    targetTokens = DEFAULT_TARGET_TOKENS,
    overlapTokens = DEFAULT_OVERLAP_TOKENS,
  } = opts;

  const filename = filenameFromPath(path);
  const indexed_at = Date.now();
  const chunks: Chunk[] = [];

  // Split on ## or ### headers (keep the header with the content)
  const headerRegex = /^(#{2,3})\s+.+$/gm;
  const sections: Array<{ header: string; body: string }> = [];
  let lastIndex = 0;
  let lastHeader = "(intro)";
  let match: RegExpExecArray | null;

  // Capture content before first header as intro
  const firstMatch = headerRegex.exec(text);
  if (firstMatch && firstMatch.index > 0) {
    const intro = text.slice(0, firstMatch.index).trim();
    if (intro.length > 50) {
      sections.push({ header: "(intro)", body: intro });
    }
    lastIndex = firstMatch.index;
    lastHeader = firstMatch[0].replace(/^#+\s+/, "");
  } else if (firstMatch === null) {
    // No headers — single section
    sections.push({ header: "(document)", body: text.trim() });
  }

  headerRegex.lastIndex = 0;
  while ((match = headerRegex.exec(text)) !== null) {
    if (match.index < lastIndex) continue;
    const headerLine = match[0].replace(/^#+\s+/, "");
    const sectionBody = text.slice(lastIndex, match.index).trim();
    if (sectionBody.length > 0) {
      sections.push({ header: lastHeader, body: sectionBody });
    }
    lastIndex = match.index;
    lastHeader = headerLine;
  }
  // Last section
  const lastBody = text.slice(lastIndex).trim();
  if (lastBody.length > 0) {
    sections.push({ header: lastHeader, body: lastBody });
  }

  // If sections are too large, sub-split by token windows with overlap
  for (const section of sections) {
    if (chunks.length >= maxChunks) break;
    const sectionTokens = estimateTokens(section.body);
    if (sectionTokens <= targetTokens * 1.5) {
      chunks.push({
        id: vectorId(library, sha256, chunks.length),
        text: `## ${section.header}\n${section.body}`,
        metadata: {
          path,
          library,
          filename,
          header: section.header,
          chunk_index: chunks.length,
          sha256,
          indexed_at,
        },
      });
    } else {
      // Sub-split by approximate token windows with overlap
      const charsPerChunk = targetTokens * 4;
      const overlapChars = overlapTokens * 4;
      let pos = 0;
      while (pos < section.body.length && chunks.length < maxChunks) {
        const end = Math.min(pos + charsPerChunk, section.body.length);
        const slice = section.body.slice(pos, end);
        chunks.push({
          id: vectorId(library, sha256, chunks.length),
          text: `## ${section.header}\n${slice}`,
          metadata: {
            path,
            library,
            filename,
            header: section.header,
            chunk_index: chunks.length,
            sha256,
            indexed_at,
          },
        });
        if (end >= section.body.length) break;
        pos = end - overlapChars;
        if (pos < 0) pos = 0;
      }
    }
  }

  return chunks;
}

/**
 * Chunk code by top-level function/class boundaries, with fallback to fixed windows.
 * Matches: export function, function, class, struct, impl, fn, def, async def, pub fn, etc.
 * Works for TS, Python, Rust, Go, Java, Swift, etc.
 */
export function chunkCode(text: string, opts: ChunkOptions): Chunk[] {
  const {
    path,
    library,
    sha256,
    maxChunks = DEFAULT_MAX_CHUNKS,
    targetTokens = DEFAULT_TARGET_TOKENS,
    overlapTokens = DEFAULT_OVERLAP_TOKENS,
  } = opts;

  const filename = filenameFromPath(path);
  const indexed_at = Date.now();
  const chunks: Chunk[] = [];

  // Detect top-level function/class/struct/impl/fn/def boundaries
  const topLevelRegex =
    /^(?:export\s+)?(?:async\s+)?(?:function|class|struct|enum|impl|fn|def|async\s+def|pub\s+(?:async\s+)?fn|pub\s+struct|pub\s+enum|pub\s+trait|trait|interface|type)\s+/gm;

  const boundaries: Array<{ start: number; header: string }> = [];
  let match: RegExpExecArray | null;
  while ((match = topLevelRegex.exec(text)) !== null) {
    // Get the first line of the declaration as the header
    const lineEnd = text.indexOf("\n", match.index);
    const header = text.slice(match.index, lineEnd > match.index ? lineEnd : match.index + 80).trim();
    boundaries.push({ start: match.index, header });
  }

  // File-level comment block / imports as first chunk
  if (boundaries.length > 0 && boundaries[0].start > 100) {
    const intro = text.slice(0, boundaries[0].start).trim();
    if (intro.length > 50) {
      chunks.push({
        id: vectorId(library, sha256, chunks.length),
        text: intro,
        metadata: {
          path,
          library,
          filename,
          header: "(header/imports)",
          chunk_index: chunks.length,
          sha256,
          indexed_at,
        },
      });
    }
  }

  // Each function/class becomes a chunk (or is sub-split if too large)
  for (let i = 0; i < boundaries.length && chunks.length < maxChunks; i++) {
    const start = boundaries[i].start;
    const end = i + 1 < boundaries.length ? boundaries[i + 1].start : text.length;
    const body = text.slice(start, end).trim();
    const tokens = estimateTokens(body);

    if (tokens <= targetTokens * 1.5) {
      chunks.push({
        id: vectorId(library, sha256, chunks.length),
        text: body,
        metadata: {
          path,
          library,
          filename,
          header: boundaries[i].header.slice(0, 120),
          chunk_index: chunks.length,
          sha256,
          indexed_at,
        },
      });
    } else {
      // Sub-split large function
      const charsPerChunk = targetTokens * 4;
      const overlapChars = overlapTokens * 4;
      let pos = 0;
      while (pos < body.length && chunks.length < maxChunks) {
        const sliceEnd = Math.min(pos + charsPerChunk, body.length);
        chunks.push({
          id: vectorId(library, sha256, chunks.length),
          text: body.slice(pos, sliceEnd),
          metadata: {
            path,
            library,
            filename,
            header: boundaries[i].header.slice(0, 120),
            chunk_index: chunks.length,
            sha256,
            indexed_at,
          },
        });
        if (sliceEnd >= body.length) break;
        pos = sliceEnd - overlapChars;
        if (pos < 0) pos = 0;
      }
    }
  }

  // Fallback: no boundaries detected -> fixed windows
  if (chunks.length === 0) {
    const charsPerChunk = targetTokens * 4;
    const overlapChars = overlapTokens * 4;
    let pos = 0;
    while (pos < text.length && chunks.length < maxChunks) {
      const end = Math.min(pos + charsPerChunk, text.length);
      chunks.push({
        id: vectorId(library, sha256, chunks.length),
        text: text.slice(pos, end),
        metadata: {
          path,
          library,
          filename,
          header: `(chunk ${chunks.length})`,
          chunk_index: chunks.length,
          sha256,
          indexed_at,
        },
      });
      if (end >= text.length) break;
      pos = end - overlapChars;
      if (pos < 0) pos = 0;
    }
  }

  return chunks;
}

/**
 * Chunk HTML by stripping tags and splitting on fixed windows.
 */
export function chunkHtml(text: string, opts: ChunkOptions): Chunk[] {
  const {
    path,
    library,
    sha256,
    maxChunks = DEFAULT_MAX_CHUNKS,
    targetTokens = DEFAULT_TARGET_TOKENS,
  } = opts;

  const filename = filenameFromPath(path);
  const indexed_at = Date.now();

  // Strip tags, decode basic entities
  const stripped = text
    .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, "")
    .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/\s+/g, " ")
    .trim();

  if (stripped.length < 50) return [];

  const charsPerChunk = targetTokens * 4;
  const chunks: Chunk[] = [];
  let pos = 0;
  while (pos < stripped.length && chunks.length < maxChunks) {
    const end = Math.min(pos + charsPerChunk, stripped.length);
    chunks.push({
      id: vectorId(library, sha256, chunks.length),
      text: stripped.slice(pos, end),
      metadata: {
        path,
        library,
        filename,
        header: `(html chunk ${chunks.length})`,
        chunk_index: chunks.length,
        sha256,
        indexed_at,
      },
    });
    if (end >= stripped.length) break;
    pos = end;
  }

  return chunks;
}

/**
 * Chunk by file type — dispatches to the right chunker.
 *
 * @returns { chunks, truncated } where truncated indicates the maxChunks cap was hit.
 */
export function chunkFile(
  text: string,
  opts: ChunkOptions,
): { chunks: Chunk[]; truncated: boolean } {
  const ext = opts.path.toLowerCase().split(".").pop() ?? "";
  let chunks: Chunk[] = [];

  if (ext === "md" || ext === "mdc" || ext === "markdown") {
    chunks = chunkMarkdown(text, opts);
  } else if (["ts", "tsx", "js", "jsx", "py", "rs", "go", "java", "kt", "swift"].includes(ext)) {
    chunks = chunkCode(text, opts);
  } else if (["html", "htm"].includes(ext)) {
    chunks = chunkHtml(text, opts);
  } else {
    // Default: treat as markdown-ish text
    chunks = chunkMarkdown(text, opts);
  }

  const truncated = chunks.length >= (opts.maxChunks ?? DEFAULT_MAX_CHUNKS);
  return { chunks, truncated };
}
