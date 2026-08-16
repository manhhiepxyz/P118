import { useEffect, useState } from 'react'

const THEME_KEY = 'p118_theme'

export type Theme = 'light' | 'dark'

/**
 * Theme dùng chung cho toàn ứng dụng.
 *
 * Tách khỏi `AppLayout` vì workspace nằm NGOÀI layout đó nhưng phải dùng đúng
 * một cơ chế: cùng khoá `localStorage`, cùng lớp `.dark` trên `<html>`. Hai bản
 * cài đặt song song thì đổi theme ở một chỗ và chỗ kia không biết.
 */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => {
    const stored = localStorage.getItem(THEME_KEY)
    if (stored === 'light' || stored === 'dark') return stored
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    localStorage.setItem(THEME_KEY, theme)
  }, [theme])

  return { theme, setTheme, toggle: () => setTheme((value) => (value === 'dark' ? 'light' : 'dark')) }
}
