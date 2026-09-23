import { lazy, Suspense } from "react";
import { Link, Route, Routes, useLocation } from "react-router";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Header } from "./components/Header";
import { EmptyState, LoadingState } from "./components/States";

// One chunk per page; the chart page also pulls in lightweight-charts, which gets its own chunk (vite.config.ts).
const ChartPage = lazy(() => import("./pages/ChartPage").then((m) => ({ default: m.ChartPage })));
const ChartRedirect = lazy(() => import("./pages/ChartPage").then((m) => ({ default: m.ChartRedirect })));
const ScannerPage = lazy(() => import("./pages/ScannerPage").then((m) => ({ default: m.ScannerPage })));
const ScorecardPage = lazy(() => import("./pages/ScorecardPage").then((m) => ({ default: m.ScorecardPage })));
const AccuracyPage = lazy(() => import("./pages/AccuracyPage").then((m) => ({ default: m.AccuracyPage })));
const NewsPage = lazy(() => import("./pages/NewsPage").then((m) => ({ default: m.NewsPage })));

function NotFound() {
  return (
    <EmptyState>
      Page not found. <Link to="/">Go to the chart</Link>.
    </EmptyState>
  );
}

export function AppRoutes() {
  // Keyed by section so a page that failed (e.g. its chunk didn't load) recovers on navigating away.
  const section = useLocation().pathname.split("/")[1];
  return (
    <ErrorBoundary key={section} label="page">
      <Suspense fallback={<LoadingState label="Loading page…" />}>
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
      </Suspense>
    </ErrorBoundary>
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
