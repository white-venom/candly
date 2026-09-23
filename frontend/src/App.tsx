import { Link, Route, Routes } from "react-router";
import { Header } from "./components/Header";
import { EmptyState } from "./components/States";
import { AccuracyPage } from "./pages/AccuracyPage";
import { ChartPage, ChartRedirect } from "./pages/ChartPage";
import { NewsPage } from "./pages/NewsPage";
import { ScannerPage } from "./pages/ScannerPage";
import { ScorecardPage } from "./pages/ScorecardPage";

function NotFound() {
  return (
    <EmptyState>
      Page not found. <Link to="/">Go to the chart</Link>.
    </EmptyState>
  );
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<ChartRedirect />} />
      <Route path="/chart" element={<ChartRedirect />} />
      <Route path="/chart/:instrument" element={<ChartRedirect />} />
      <Route path="/chart/:instrument/:tf" element={<ChartPage />} />
      <Route path="/scanner" element={<ScannerPage />} />
      <Route path="/scorecard" element={<ScorecardPage />} />
      <Route path="/accuracy" element={<AccuracyPage />} />
      <Route path="/news" element={<NewsPage />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}

export default function App() {
  return (
    <div className="min-h-svh bg-page text-ink">
      <Header />
      <main className="mx-auto max-w-[1600px] p-4">
        <AppRoutes />
      </main>
    </div>
  );
}
