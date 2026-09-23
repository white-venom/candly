import { useLocation } from "react-router";

/** Renders the router location so tests can assert on navigation. */
export function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search}</output>;
}
