import type { ComponentPropsWithoutRef } from 'react';
import type { ExtraProps } from 'react-markdown';

/**
 * A Markdown table inside its own box, which on a phone scrolls sideways when
 * the table is wider than the column (globals.css `.table-scroll`). The table
 * itself stays a table, so a narrow one still spans the column.
 */
export default function ScrollingTable({ node, ...props }: ComponentPropsWithoutRef<'table'> & ExtraProps) {
  return (
    <div className="table-scroll">
      <table {...props} />
    </div>
  );
}
