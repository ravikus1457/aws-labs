output "vpc_id" {
  description = "ID of the lab VPC"
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "Public subnet IDs (route to Internet Gateway)"
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Private subnet IDs (route to NAT Gateway)"
  value       = aws_subnet.private[*].id
}

output "nat_gateway_id" {
  value = aws_nat_gateway.nat.id
}

output "web_security_group_id" {
  value = aws_security_group.web.id
}
