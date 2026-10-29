import { redirect } from 'next/navigation';

// The platform simulator moved to Developer mode.
export default function SimRedirect() {
  redirect('/dev/sim');
}
