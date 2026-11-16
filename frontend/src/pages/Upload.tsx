import { useCallback, useState } from 'react'
import { useDropzone } from 'react-dropzone'
import { useNavigate } from 'react-router-dom'
import { uploadFile, mapColumns, getModelSummary, uploadCaseLog, type ColumnMapping } from '@/api/client'
import { useSession } from '@/store/sessionStore'
import { Upload, FileText, Loader2, ChevronRight, TableProperties, X } from 'lucide-react'

const NONE = '(none)'

export default function UploadPage() {
  const navigate = useNavigate()
  const { setSessionId, setModelSummary, reset } = useSession()

  const [status, setStatus] = useState<'idle' | 'uploading' | 'mapping' | 'extracting' | 'error'>('idle')
  const [error, setError] = useState('')
  const [columns, setColumns] = useState<string[]>([])
  const [detected, setDetected] = useState<Record<string, string | null>>({})
  const [sessionId, setLocalSessionId] = useState('')
  const [rowCount, setRowCount] = useState(0)

  const [mapping, setMapping] = useState<Record<string, string>>({
    case_id: '', activity: '', start_time: NONE, end_time: NONE, resource: NONE, source: NONE,
  })

  // Case log state
  const [caseLogFile, setCaseLogFile] = useState<File | null>(null)
  const [caseLogUploaded, setCaseLogUploaded] = useState(false)
  const [caseLogCols, setCaseLogCols] = useState<string[]>([])
  const [caseLogDetectedId, setCaseLogDetectedId] = useState('')
  const [caseLogRowCount, setCaseLogRowCount] = useState(0)
  const [caseLogError, setCaseLogError] = useState('')
  const [caseLogUploading, setCaseLogUploading] = useState(false)

  const onDrop = useCallback(async (accepted: File[]) => {
    if (!accepted.length) return
    setStatus('uploading')
    setError('')
    try {
      const res = await uploadFile(accepted[0])
      setLocalSessionId(res.session_id)
      setRowCount(res.row_count)
      setColumns(res.columns)
      setDetected(res.detected_mapping)
      setMapping({
        case_id: res.detected_mapping.case_id || res.columns[0] || '',
        activity: res.detected_mapping.activity || res.columns[1] || '',
        start_time: res.detected_mapping.start_time || NONE,
        end_time: res.detected_mapping.end_time || NONE,
        resource: res.detected_mapping.resource || NONE,
        source: res.detected_mapping.source || NONE,
      })
      setStatus('mapping')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e))
      setStatus('error')
    }
  }, [])

  const onCaseLogDrop = useCallback(async (accepted: File[]) => {
    if (!accepted.length || !sessionId) return
    const file = accepted[0]
    setCaseLogFile(file)
    setCaseLogError('')
    setCaseLogUploading(true)
    try {
      const res = await uploadCaseLog(sessionId, file)
      setCaseLogCols(res.columns)
      setCaseLogDetectedId(res.detected_case_id)
      setCaseLogRowCount(res.row_count)
      setCaseLogUploaded(true)
    } catch (e: unknown) {
      setCaseLogFile(null)
      setCaseLogError(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setCaseLogUploading(false)
    }
  }, [sessionId])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop, accept: { 'text/csv': ['.csv'], 'application/vnd.ms-excel': ['.xls'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'] },
    multiple: false,
  })

  const { getRootProps: getCaseLogRootProps, getInputProps: getCaseLogInputProps, isDragActive: isCaseLogDragActive } = useDropzone({
    onDrop: onCaseLogDrop,
    accept: { 'text/csv': ['.csv'], 'application/vnd.ms-excel': ['.xls'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'] },
    multiple: false,
  })

  const handleExtract = async () => {
    setStatus('extracting')
    try {
      const cm: ColumnMapping = { case_id: mapping.case_id, activity: mapping.activity }
      if (mapping.start_time !== NONE) cm.start_time = mapping.start_time
      if (mapping.end_time !== NONE) cm.end_time = mapping.end_time
      if (mapping.resource !== NONE) cm.resource = mapping.resource
      if (mapping.source !== NONE) cm.source = mapping.source

      reset()
      await mapColumns(sessionId, cm, caseLogUploaded ? caseLogDetectedId : undefined)
      const summary = await getModelSummary(sessionId)
      setSessionId(sessionId)
      setModelSummary(summary)
      navigate('/workspace/learnings')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e))
      setStatus('error')
    }
  }

  const required = ['case_id', 'activity'] as const
  const optional = ['start_time', 'end_time', 'resource', 'source'] as const
  const allCols = [NONE, ...columns]

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      <div className="flex-1 flex items-start justify-center pt-6 px-4">
        <div className="w-full max-w-2xl">
        {/* Header */}
        <div className="mb-6 text-center">
          <h1 className="text-3xl font-bold text-gray-900 tracking-tight">OBSERVE</h1>
          <p className="mt-1 text-gray-500">Upload an event log to extract process behavior observations and run simulations</p>
        </div>

        {/* Drop zone */}
        {status === 'idle' && (
          <div
            {...getRootProps()}
            className={`border-2 border-dashed rounded-none p-12 text-center cursor-pointer transition-all ${
              isDragActive ? 'border-gray-600 bg-gray-50' : 'border-gray-300 bg-white hover:border-gray-400 hover:bg-gray-50/30'
            }`}
          >
            <input {...getInputProps()} />
            <Upload className="w-10 h-10 mx-auto text-gray-400 mb-4" />
            <p className="text-lg font-medium text-gray-700">
              {isDragActive ? 'Drop your file here' : 'Drag & drop an event log'}
            </p>
            <p className="text-sm text-gray-400 mt-1">CSV, XLS, or XLSX · click to browse</p>
          </div>
        )}

        {/* Uploading spinner */}
        {status === 'uploading' && (
          <div className="bg-white rounded-none p-12 text-center shadow-sm border border-gray-100">
            <Loader2 className="w-8 h-8 mx-auto animate-spin text-gray-800 mb-3" />
            <p className="text-gray-600 font-medium">Uploading file…</p>
          </div>
        )}

        {/* Column mapping */}
        {(status === 'mapping' || status === 'extracting') && (
          <div className="space-y-4">
            {/* Event log mapping card */}
            <div className="bg-white rounded-none shadow-sm border border-gray-100 overflow-hidden">
              <div className="px-6 py-5 border-b border-gray-100 flex items-center gap-3">
                <FileText className="w-5 h-5 text-gray-800" />
                <div>
                  <h2 className="font-semibold text-gray-900">Event Log — Column Mapping</h2>
                  <p className="text-sm text-gray-400">{rowCount.toLocaleString()} rows loaded</p>
                </div>
              </div>
              <div className="p-6 space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  {required.map(field => (
                    <div key={field}>
                      <label className="block text-sm font-medium text-gray-700 mb-1 capitalize">
                        {field.replace('_', ' ')} <span className="text-gray-600">*</span>
                      </label>
                      <select
                        value={mapping[field]}
                        onChange={e => setMapping(m => ({ ...m, [field]: e.target.value }))}
                        className="w-full rounded-none border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600"
                      >
                        {columns.map(c => <option key={c} value={c}>{c}</option>)}
                      </select>
                      {detected[field] && <p className="text-xs text-gray-600 mt-1">Auto-detected: {detected[field]}</p>}
                    </div>
                  ))}
                </div>
                <div className="grid grid-cols-2 gap-4">
                  {optional.map(field => (
                    <div key={field}>
                      <label className="block text-sm font-medium text-gray-600 mb-1 capitalize">
                        {field.replace('_', ' ')}
                      </label>
                      <select
                        value={mapping[field]}
                        onChange={e => setMapping(m => ({ ...m, [field]: e.target.value }))}
                        className="w-full rounded-none border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600 text-gray-700"
                      >
                        {allCols.map(c => <option key={c} value={c}>{c}</option>)}
                      </select>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* Case log optional card */}
            <div className="bg-white rounded-none shadow-sm border border-gray-100 overflow-hidden">
              <div className="px-6 py-4 border-b border-gray-100 flex items-center gap-3">
                <TableProperties className="w-5 h-5 text-gray-600" />
                <div className="flex-1">
                  <h2 className="font-semibold text-gray-900">Case Log <span className="text-xs font-normal text-gray-400 ml-1">optional</span></h2>
                  <p className="text-xs text-gray-400 mt-0.5">One row per case with attributes such as region, customer tier, or case type.</p>
                </div>
                {caseLogUploaded && (
                  <button onClick={() => { setCaseLogFile(null); setCaseLogUploaded(false); setCaseLogCols([]); setCaseLogRowCount(0) }}
                    className="text-gray-300 hover:text-gray-400 transition-colors">
                    <X className="w-4 h-4" />
                  </button>
                )}
              </div>
              <div className="p-6">
                {!caseLogUploaded ? (
                  <>
                    <div {...getCaseLogRootProps()} className={`border-2 border-dashed rounded-none p-6 text-center cursor-pointer transition-all ${
                      isCaseLogDragActive ? 'border-gray-400 bg-gray-50' : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50/30'
                    }`}>
                      <input {...getCaseLogInputProps()} />
                      {caseLogUploading ? (
                        <>
                          <Loader2 className="w-7 h-7 mx-auto animate-spin text-gray-600 mb-2" />
                          <p className="text-sm text-gray-600">Uploading…</p>
                        </>
                      ) : (
                        <>
                          <TableProperties className="w-7 h-7 mx-auto text-gray-300 mb-2" />
                          <p className="text-sm text-gray-500">{isCaseLogDragActive ? 'Drop case log here' : 'Drag & drop case log · CSV, XLS, XLSX'}</p>
                        </>
                      )}
                    </div>
                    {caseLogError && (
                      <p className="mt-2 text-xs text-gray-600 flex items-center gap-1">
                        <span className="font-medium">Upload failed:</span> {caseLogError}
                      </p>
                    )}
                  </>
                ) : (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 text-sm text-gray-800 font-medium">
                        <div className="w-2 h-2 rounded-none bg-gray-600" />
                        {caseLogFile?.name}
                      </div>
                      <span className="text-xs text-gray-400">{caseLogRowCount.toLocaleString()} rows · {caseLogCols.length} columns</span>
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">Case ID column in case log</label>
                      <select
                        value={caseLogDetectedId}
                        onChange={e => setCaseLogDetectedId(e.target.value)}
                        className="w-full rounded-none border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600"
                      >
                        {caseLogCols.map(c => <option key={c} value={c}>{c}</option>)}
                      </select>
                      <p className="text-xs text-gray-600 mt-1">Must match the case ID in the event log</p>
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Actions */}
            <div className="flex justify-between items-center">
              <button
                onClick={() => { setStatus('idle'); setColumns([]); setCaseLogFile(null); setCaseLogUploaded(false) }}
                className="text-sm text-gray-500 hover:text-gray-700"
              >
                ← Start over
              </button>
              <button
                onClick={handleExtract}
                disabled={!mapping.case_id || !mapping.activity || status === 'extracting'}
                className="flex items-center gap-2 px-5 py-2.5 rounded-none bg-gray-800 text-white text-sm font-medium hover:bg-gray-900 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
              >
                {status === 'extracting' ? (
                  <><Loader2 className="w-4 h-4 animate-spin" /> Discovering observations…</>
                ) : (
                  <>Discover Process Behavior Observations & Simulation Model <ChevronRight className="w-4 h-4" /></>
                )}
              </button>
            </div>
          </div>
        )}

        {/* Error */}
        {status === 'error' && (
          <div className="bg-gray-50 border border-gray-200 rounded-none p-6 text-center">
            <p className="text-gray-900 font-medium mb-3">Something went wrong</p>
            <p className="text-gray-600 text-sm mb-4">{error}</p>
            <button onClick={() => setStatus('idle')} className="px-4 py-2 rounded-none bg-gray-800 text-white text-sm hover:bg-gray-900">
              Try again
            </button>
          </div>
        )}
        </div>
      </div>
    </div>
  )
}
