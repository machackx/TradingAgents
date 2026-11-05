import { NODE_STATUS_LABELS, RATING_TONE, RUN_STATUS_LABELS, ratingLabel } from "../labels";

export function RatingBadge({ rating, large = false }: { rating?: string | null; large?: boolean }) {
  if (!rating) return null;
  const tone = RATING_TONE[rating] ?? "level";
  return (
    <span className={`rating rating-${tone}${large ? " rating-large" : ""}`} title={rating}>
      {ratingLabel(rating)}
    </span>
  );
}

export function RunStatusPill({ status }: { status: string }) {
  return <span className={`pill pill-${status}`}>{RUN_STATUS_LABELS[status] ?? status}</span>;
}

export function NodeStatusDot({ status }: { status: string }) {
  return <span className={`dot dot-${status}`} aria-label={NODE_STATUS_LABELS[status] ?? status} />;
}
