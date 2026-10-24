import { useContext } from 'react'
import { ProjectContext, type ProjectContextValue } from './context'

export function useProject(): ProjectContextValue {
  const value = useContext(ProjectContext)
  if (!value) {
    throw new Error('useProject must be used inside ProjectProvider')
  }
  return value
}
