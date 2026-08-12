import { redirect } from "next/navigation";

// The front-desk QUEUE is the landing page, not the graph. The product is what happens at the
// counter; the knowledge graph is the thing behind it. Someone opening this app cold should see
// the work waiting for them, not a visualisation.
export default function Home() {
  redirect("/frontdesk");
}
