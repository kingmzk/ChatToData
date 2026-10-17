import React, { useState, useMemo } from 'react';
import { Table as TableIcon, Search, Download } from 'lucide-react';

export default function DataTable({ table, turnId }) {
  const [search, setSearch] = useState('');
  const [sortCol, setSortCol] = useState(null);
  const [sortAsc, setSortAsc] = useState(true);

  if (!table || !table.columns || table.columns.length === 0) return null;

  const filteredRows = useMemo(() => {
    let rows = table.rows || [];
    if (search.trim()) {
      const q = search.toLowerCase();
      rows = rows.filter(row => {
        const values = Array.isArray(row) ? row : Object.values(row);
        return values.some(val => val !== null && val !== undefined && String(val).toLowerCase().includes(q));
      });
    }
    if (sortCol !== null && table.columns && table.columns[sortCol]) {
      const colName = table.columns[sortCol];
      rows = [...rows].sort((a, b) => {
        const valA = Array.isArray(a) ? a[sortCol] : (a[colName] !== undefined ? a[colName] : a[colName.toLowerCase()]);
        const valB = Array.isArray(b) ? b[sortCol] : (b[colName] !== undefined ? b[colName] : b[colName.toLowerCase()]);
        if (valA === valB) return 0;
        if (valA === null || valA === undefined) return 1;
        if (valB === null || valB === undefined) return -1;
        const numA = Number(valA);
        const numB = Number(valB);
        if (!isNaN(numA) && !isNaN(numB)) {
          return sortAsc ? numA - numB : numB - numA;
        }
        return sortAsc ? String(valA).localeCompare(String(valB)) : String(valB).localeCompare(String(valA));
      });
    }
    return rows;
  }, [table.rows, table.columns, search, sortCol, sortAsc]);

  const handleHeaderClick = (colIdx) => {
    if (sortCol === colIdx) {
      setSortAsc(!sortAsc);
    } else {
      setSortCol(colIdx);
      setSortAsc(true);
    }
  };

  return (
    <div className="table-card">
      <div className="table-controls">
        <div className="table-title">
          <TableIcon size={18} color="var(--accent-blue)" />
          <span>Verified Analytical Table</span>
          <span className="row-count-badge">{filteredRows.length} rows</span>
        </div>
        <div className="table-actions">
          <div className="search-input-wrap">
            <Search size={14} className="search-icon" />
            <input
              type="text"
              placeholder="Search table rows..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="table-search-input"
            />
          </div>
          {turnId && (
            <div className="export-links-group">
              <a
                href={`/api/v1/turns/${turnId}/export/xlsx`}
                download
                className="btn-export"
                title="Export formatted Excel worksheet"
              >
                <Download size={13} />
                <span>Export XLSX</span>
              </a>
              <a
                href={`/api/v1/turns/${turnId}/export/csv`}
                download
                className="btn-export"
                title="Export RFC-4180 CSV file"
              >
                <Download size={13} />
                <span>Export CSV</span>
              </a>
            </div>
          )}
        </div>
      </div>
      <div className="table-scroll-container">
        <table className="analytical-table">
          <thead>
            <tr>
              {table.columns.map((col, idx) => (
                <th
                  key={idx}
                  onClick={() => handleHeaderClick(idx)}
                  className={`sortable-th ${sortCol === idx ? 'sorted' : ''}`}
                >
                  <div className="th-content">
                    <span>{col}</span>
                    <span className="sort-indicator">
                      {sortCol === idx ? (sortAsc ? ' ▲' : ' ▼') : ' ↕'}
                    </span>
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filteredRows.map((row, rIdx) => {
              const cells = Array.isArray(row) 
                ? row 
                : table.columns.map(col => (row[col] !== undefined ? row[col] : (row[col.toLowerCase()] !== undefined ? row[col.toLowerCase()] : '')));
              return (
                <tr key={rIdx}>
                  {cells.map((cell, cIdx) => (
                    <td key={cIdx}>
                      {cell === null || cell === undefined ? (
                        <span className="suppression-tag">🔒 Suppressed</span>
                      ) : (
                        String(cell)
                      )}
                    </td>
                  ))}
                </tr>
              );
            })}
            {filteredRows.length === 0 && (
              <tr>
                <td colSpan={table.columns.length} style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-dim)' }}>
                  No rows matching "{search}"
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
