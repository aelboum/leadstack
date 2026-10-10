import { PublicBooking } from "@/components/PublicBooking";

export default async function PublicBookingPage({ params }: { params: Promise<{ publicKey: string }> }) {
  const { publicKey } = await params;
  return <PublicBooking publicKey={publicKey} />;
}
