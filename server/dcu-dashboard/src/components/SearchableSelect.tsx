'use client';

import { KeyboardEvent, MouseEvent, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';

export interface SelectOption {
  value: string;
  label: string;
}

interface Props {
  options: SelectOption[];
  value: string;
  onChange: (value: string) => void;
  ariaLabel: string;
  placeholder?: string; // 검색할 때 안내 문구 (기본: 지금 선택된 항목 이름)
  className?: string;
}

/** 검색 비교용: 대소문자·공백 무시 */
const normalize = (text: string) => text.toLowerCase().replace(/\s+/g, '');

/** 목록 안을 눌러도 입력 상자의 포커스를 뺏지 않는다 (포커스가 빠지면 목록이 닫힘) */
const keepInputFocus = (e: MouseEvent) => e.preventDefault();

/**
 * 검색할 수 있는 콤보박스.
 * ▾ 버튼이나 상자를 누르면 전체 목록이 펼쳐지고, 상자에 입력하면 목록이 바로 걸러진다.
 * 키보드: ↑↓ 이동, Enter 선택, Esc 취소(원래 선택 유지). 목록 밖을 누르면 닫힌다.
 */
export default function SearchableSelect({ options, value, onChange, ariaLabel, placeholder, className }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const listId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const selected = options.find((o) => o.value === value);
  const filtered = useMemo(() => {
    const q = normalize(query);
    return q ? options.filter((o) => normalize(`${o.label} ${o.value}`).includes(q)) : options;
  }, [options, query]);
  const activeIndex = Math.min(active, filtered.length - 1);
  const listShown = open && filtered.length > 0;
  const optionId = (i: number) => `${listId}-opt-${i}`;

  // 키보드로 고른 항목이 목록 밖에 있으면 보이도록 스크롤
  useEffect(() => {
    if (open && activeIndex >= 0) document.getElementById(`${listId}-opt-${activeIndex}`)?.scrollIntoView({ block: 'nearest' });
  }, [open, activeIndex, listId]);

  // 선택·Esc 로 닫힌 뒤에도 포커스가 상자에 있으면 이름 전체를 선택해 둔다
  // (바로 입력하면 이름 뒤에 덧붙지 않고 새 검색어로 바뀜)
  useLayoutEffect(() => {
    if (!open && document.activeElement === inputRef.current) inputRef.current?.select();
  }, [open]);

  function openList() {
    setOpen(true);
    setQuery('');
    setActive(Math.max(0, options.findIndex((o) => o.value === value))); // 지금 선택된 항목부터
  }

  function close() {
    setOpen(false);
    setQuery('');
  }

  function choose(option: SelectOption) {
    onChange(option.value);
    close();
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    // 한글 입력 조합 중(Enter·방향키로 글자를 확정하는 중)에는 목록 조작을 하지 않는다
    if (e.nativeEvent.isComposing) return;
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        if (!open) openList();
        else setActive(Math.min(activeIndex + 1, filtered.length - 1));
        break;
      case 'ArrowUp':
        e.preventDefault();
        if (!open) openList();
        else setActive(Math.max(activeIndex - 1, 0));
        break;
      case 'Enter':
        if (open) {
          e.preventDefault();
          if (filtered[activeIndex]) choose(filtered[activeIndex]);
        }
        break;
      case 'Escape':
        if (open) {
          e.preventDefault();
          close();
        }
        break;
      case 'Tab':
        close();
        break;
    }
  }

  return (
    <div
      ref={rootRef}
      className={`searchable-select ${open ? 'open' : ''} ${className ?? ''}`}
      // 포커스가 이 상자 밖으로 나가면 닫는다 (목록 안을 누를 때는 포커스를 가져가지 않음)
      onBlur={(e) => {
        if (!rootRef.current?.contains(e.relatedTarget as Node | null)) close();
      }}
    >
      <input
        ref={inputRef}
        className="searchable-select-input"
        role="combobox"
        aria-label={ariaLabel}
        aria-expanded={listShown}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={listShown && activeIndex >= 0 ? optionId(activeIndex) : undefined}
        autoComplete="off"
        spellCheck={false}
        // 닫혀 있으면 선택된 항목 이름(목록을 아직 못 불러왔으면 값 그대로), 열려 있으면 검색어
        value={open ? query : (selected?.label ?? value)}
        placeholder={open ? (placeholder ?? selected?.label ?? '검색') : placeholder}
        onChange={(e) => {
          setQuery(e.target.value);
          setActive(0);
          setOpen(true);
        }}
        onClick={() => (open ? undefined : openList())}
        onKeyDown={handleKeyDown}
      />
      <button
        type="button"
        className="searchable-select-toggle"
        tabIndex={-1}
        aria-label={open ? '목록 닫기' : '목록 열기'}
        aria-expanded={listShown}
        aria-controls={listId}
        onMouseDown={keepInputFocus}
        onClick={() => {
          if (open) close();
          else {
            openList();
            inputRef.current?.focus();
          }
        }}
      >
        ▾
      </button>
      {listShown && (
        <ul id={listId} role="listbox" aria-label={ariaLabel} className="searchable-select-list" onMouseDown={keepInputFocus}>
          {filtered.map((o, i) => (
            <li
              key={o.value}
              id={optionId(i)}
              role="option"
              aria-selected={o.value === value}
              className={`searchable-select-option ${i === activeIndex ? 'active' : ''}`}
              // 포인터를 실제로 움직였을 때만 하이라이트 (키보드로 옮겨 목록이 스크롤돼도 포인터 아래로 튀지 않게)
              onMouseMove={() => {
                if (i !== activeIndex) setActive(i);
              }}
              onClick={() => choose(o)}
            >
              {o.label}
            </li>
          ))}
        </ul>
      )}
      {open && filtered.length === 0 && (
        <div role="status" className="searchable-select-list searchable-select-empty" onMouseDown={keepInputFocus}>
          검색 결과 없음
        </div>
      )}
    </div>
  );
}
