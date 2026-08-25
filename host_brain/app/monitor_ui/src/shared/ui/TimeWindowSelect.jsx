
export default function TimeWindowSelect({
  value,
  onChange,
  options = [],
  disabled = false,
  className = 'text-xs border rounded px-2 py-1.5',
}) {
  return (
    <select
      className={className}
      value={value}
      onChange={(event) => onChange(Number(event.target.value))}
      disabled={disabled}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  )
}
