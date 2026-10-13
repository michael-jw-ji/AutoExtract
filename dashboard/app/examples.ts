// Built-in playground documents. Empty until Form 4 examples exist: add
// entries of this shape (text = rendered filing, gold = parsed XML).

export type Example = {
  id: string;
  label: string;
  caption: string;
  domain: string;
  text: string;
  gold: Record<string, unknown> | null;
};

export const EXAMPLES: Example[] = [];
