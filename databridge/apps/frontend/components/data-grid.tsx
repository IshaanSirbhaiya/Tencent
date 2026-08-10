"use client";
import * as React from "react";
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table";
import { cn } from "@/lib/utils";

// One headless TanStack Table primitive under every grid (D-FE-3). Rows are server-bounded
// (auto-LIMIT), so the worst case is small; a max-height scroll keeps the DOM contained.
export function DataGrid<T>({
  data,
  columns,
  empty = "No rows.",
  maxHeight = 420,
}: {
  data: T[];
  columns: ColumnDef<T, unknown>[];
  empty?: string;
  maxHeight?: number;
}) {
  const table = useReactTable({ data, columns, getCoreRowModel: getCoreRowModel() });
  return (
    <div className="overflow-auto rounded border border-border" style={{ maxHeight }}>
      <table className="w-full border-collapse text-xs">
        <thead className="sticky top-0 z-10 bg-elevated">
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((h) => (
                <th
                  key={h.id}
                  className="border-b border-border px-3 py-2 text-left font-medium text-subtle"
                >
                  {h.isPlaceholder ? null : flexRender(h.column.columnDef.header, h.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="px-3 py-6 text-center text-subtle">
                {empty}
              </td>
            </tr>
          ) : (
            table.getRowModel().rows.map((row, i) => (
              <tr key={row.id} className={cn(i % 2 ? "bg-surface" : "bg-transparent")}>
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="border-b border-border/50 px-3 py-1.5 align-top">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
