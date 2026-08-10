import { redirect } from "next/navigation";

// The Company Brain is the investor-first landing (T08). The conversion pipeline routes remain in
// the sidebar.
export default function Home() {
  redirect("/brain");
}
