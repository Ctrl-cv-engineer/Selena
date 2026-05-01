import { BrowserRouter as Router, Routes, Route, Navigate } from "react-router-dom";
import Layout from "./components/Layout/Layout";
import Chat from "./pages/Chat";
import Debug from "./pages/Debug";
import DataVisualization from "./pages/DataVisualization";
import IntentionSelection from "./pages/IntentionSelection";
import Schedule from "./pages/Schedule";
import ConfigEditor from "./pages/ConfigEditor";
import LLMInspector from "./pages/LLMInspector";
import ATMInspector from "./pages/ATMInspector";
import Workbench from "./pages/Workbench";

export default function App() {
  return (
    <Router>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Chat />} />
          <Route path="workbench" element={<Workbench />} />
          <Route path="debug" element={<Debug />} />
          <Route path="IntentionSelection" element={<IntentionSelection />} />
          <Route path="data" element={<Navigate to="/data/default" replace />} />
          <Route path="data/:collectionName" element={<DataVisualization />} />
          <Route path="schedule" element={<Schedule />} />
          <Route path="config" element={<ConfigEditor />} />
          <Route path="llm-inspector" element={<LLMInspector />} />
          <Route path="atm-inspector" element={<ATMInspector />} />
        </Route>
      </Routes>
    </Router>
  );
}
