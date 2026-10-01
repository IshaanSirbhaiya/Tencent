/**
 * `/agents` — the build plan's demo script names this URL. Agents are built and run in the Agents
 * tab of `/builder` (one surface, not a second copy of it), so this redirects there rather than
 * rendering a duplicate.
 */
import { redirect } from "next/navigation";

export default function AgentsRedirect() {
  redirect("/builder?tab=agents");
}
