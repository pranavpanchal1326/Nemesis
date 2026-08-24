export default async function Track({ params }: { params: Promise<{ complaintId: string }> }) {
  const { complaintId } = await params;
  return <h1>{complaintId}</h1>;
}
