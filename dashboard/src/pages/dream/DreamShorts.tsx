import { dreamApi } from '../../dreamApi'
import { usePoll } from '../../hooks'
import { DreamList } from './DreamList'
import { DreamView } from './DreamView'
import { Lessons } from './Lessons'
import { NewDreamForm } from './NewDreamForm'

/** Dream Shorts (docs/plans/dream-shorts.md): list + new-Short form, or one Short's steps. */
export function DreamShorts({ id }: { id?: string }) {
  return id ? <DreamView id={id} /> : <DreamHome />
}

function DreamHome() {
  const { data: shorts, error, refresh } = usePoll(dreamApi.list, 5000)
  return (
    <>
      <NewDreamForm onSettled={() => void refresh()} />
      <Lessons />
      <DreamList shorts={shorts} error={error} refresh={() => void refresh()} />
    </>
  )
}
