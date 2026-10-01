import { redirect } from "next/navigation";

// The PLATFORM is the landing page, not a pack's operator queue. Someone opening this app cold
// should see the substrate — who asked, under what authority, what was withheld — because that is
// what the product is. A pack's operator surfaces are one view of it and are two clicks away in
// the sidebar, under the pack switcher.
//
// (This used to redirect to the first pack's queue, from when the vertical WAS the product.)
export default function Home() {
  redirect("/trace");
}
