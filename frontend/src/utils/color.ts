const PALETTE = [
  '#6c8cff',
  '#7d6cff',
  '#5aa8c7',
  '#4ca88a',
  '#a06bb4',
  '#d98b4f',
  '#c7a04c',
  '#6f9d5e',
  '#d1607a',
  '#5f86c9',
  '#a871d1',
  '#4fbfa5',
]

export function accentForName(name: string): string {
  let h = 0
  for (let i = 0; i < name.length; i++) {
    h = (h * 31 + name.charCodeAt(i)) | 0
  }
  return PALETTE[Math.abs(h) % PALETTE.length]
}
